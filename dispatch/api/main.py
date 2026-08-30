# api/main.py — ZONE 2 <-> ZONE 3 BOUNDARY
#
# Read-only mirror of the fab. Consumes Kafka, serves the UI.
#
# THE RULE: this service has NO write path to the dispatcher. It produces to no
# topic and opens no socket into zone 1. Scenario runs shell out to the C++
# fab_scenario binary against a CLONED registry, never the live planner.
# A React button must not be able to retune a running fab.

import json
import os
import sys
import queue
import subprocess
import threading
import time
from collections import deque, Counter, defaultdict
from datetime import datetime, timezone

from flask import Flask, Response, jsonify, request
from flask_cors import CORS

from assistant import FabAssistant

app = Flask(__name__)
CORS(app, resources={r"/*": {"origins": os.getenv("CORS_ORIGINS", "*")}})

KAFKA_BROKERS = os.getenv("KAFKA_BROKERS", "kafka:9092")
READ_ONLY     = os.getenv("READ_ONLY", "true").lower() == "true"
SCENARIO_BIN  = os.getenv("SCENARIO_BIN", "/app/fab_scenario")
TOOL_CONFIG   = os.getenv("TOOL_CONFIG", "/etc/fab/fab_tools.json")
SOLVER        = os.getenv("SOLVER", "cpsat")
# Local dev only: synthesise a ready pool when no feed is attached, so the
# dashboard's scenario button does something. Never enable this where an empty
# pool would be a real signal.
DEMO_LOTS     = os.getenv("DEMO_LOTS", "0").lower() in ("1", "true", "yes")
# Tail a newline-delimited event file instead of Kafka. Same consume-only role,
# different transport -- lets the stack run end to end without a broker.
FEED_FILE     = os.getenv("FEED_FILE", "")
# Burndown points held in memory. One bounded ring for the whole fab rather
# than a per-lot series with an eviction policy: LVHM emits ~23k progress
# events per simulated day across ~2k lots in flight, so per-lot retention
# either leaks or silently drops the lots you were watching. A single ring is
# a flat, predictable memory ceiling, and cohorts are grouped out of it on
# demand -- the client fetches once and appends from SSE thereafter.
BURNDOWN_MAX  = int(os.getenv("BURNDOWN_MAX", "150000"))
# Playback control for the simulator feed (bench/tools/sim_feed.py), which is
# a separate host process. The dashboard's speed menu writes here and the feed
# polls it. This is NOT a write path into the fab: it changes only how fast a
# simulated run is replayed, so the zone-3 read-only rule still holds -- there
# is no reachable dispatcher on the other end of it.
SIM_CONTROL_FILE = os.getenv(
    "SIM_CONTROL_FILE",
    os.path.join(os.path.dirname(os.path.abspath(__file__)),
                 "..", "..", "bench", ".sim_control.json"))
# What the dashboard offers. 0 would mean unpaced, which floods the browser,
# so it is deliberately not on the menu.
SIM_SPEEDS = [1, 10, 20, 50, 100, 400]

# ---------------------------------------------------------------------------
# Live state, rebuilt from the Kafka event stream.
# Bounded everywhere: an unbounded buffer in a long-running mirror is a leak.
# ---------------------------------------------------------------------------

def _as_int(v, default=None):
    """The wire format is all strings; never let one crash the ingest thread."""
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def _as_float(v, default=None):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def tool_group(tool_id):
    """Group a tool by type from its id.

    Two naming schemes reach us: the C++ config uses ETCH_11, CVD_07; the
    simulator feed uses Litho_BE_110_947. Stripping one trailing _<digits>
    handles both -- ETCH_11 -> ETCH, Litho_BE_110_947 -> Litho_BE_110 -- and
    leaves anything unnumbered as its own group rather than guessing.
    """
    if not tool_id:
        return "unknown"
    head, sep, tail = tool_id.rpartition("_")
    if sep and tail.isdigit() and head:
        return head
    return tool_id


class FabMirror:
    def __init__(self, maxlen=2000):
        self.lock       = threading.Lock()
        self.events     = deque(maxlen=maxlen)
        self.tools      = {}          # tool_id -> {online, last_seen}
        self.lots_ready = {}          # lot_id  -> lot dict
        self.in_flight  = {}          # lot_id  -> tool_id
        self.counts     = Counter()
        self.decisions  = deque(maxlen=500)
        self.throughput = deque(maxlen=300)   # (ts, completed_total)
        self.subscribers = []                 # SSE queues
        # Per-tool rollup. The tool view renders this rather than recomputing
        # from the feed in the browser: two derivations of the same number
        # drift, and then nobody knows which screen is lying.
        self.tool_stats = defaultdict(lambda: {
            "dispatches": 0,     # decisions seen for this tool
            "lots": 0,           # lots dispatched onto it
            "started": 0,        # LOT_STARTED events
            "completed": 0,      # LOT_COMPLETE from a lot it was running
            "queue": None,       # queue depth at the last decision
            "setup": None,       # setup at the last decision
            "last_day": None,    # simulated day of the last decision
            "last_ts": None,     # wall clock, so staleness is visible
            "changeovers": 0,
        })
        # Per-tool decision ring. The shared `decisions` deque is global and
        # 500 deep; with a few hundred tools a given tool's last decision is
        # evicted long before anyone drills into it, so the detail view would
        # always show an empty history. Bounded per tool, so still no leak.
        self.tool_recent = defaultdict(lambda: deque(maxlen=40))
        # Cohort burndown. `burndown` holds compact points; `lot_meta` holds the
        # per-lot constants (cohort, route length, due date) that would
        # otherwise be repeated on every point.
        self.burndown = deque(maxlen=BURNDOWN_MAX)
        self.lot_meta = {}
        self.sim_t = None        # latest simulated time seen, for the "now" rule

    def apply(self, topic, ev):
        with self.lock:
            self.events.append({"topic": topic, **ev})
            t = ev.get("type")
            self.counts[t] += 1

            if t == "LOT_READY":
                self.lots_ready[ev.get("lot")] = ev
            elif t == "LOT_STARTED":
                lot = ev.get("lot")
                self.lots_ready.pop(lot, None)
                self.in_flight[lot] = ev.get("tool")
                if ev.get("tool"):
                    self.tool_stats[ev["tool"]]["started"] += 1
            elif t == "LOT_COMPLETE":
                # Credit the completion to whichever tool was running it, which
                # only in_flight knows -- the event itself carries no tool.
                tool = self.in_flight.pop(ev.get("lot"), None)
                if tool:
                    self.tool_stats[tool]["completed"] += 1
                self.throughput.append((time.time(), self.counts["LOT_COMPLETE"]))
            elif t == "LOT_PROGRESS":
                self._apply_progress(ev)
            elif t == "TOOL_STATUS":
                self.tools[ev.get("tool")] = {
                    "online": ev.get("online") != "0",
                    "last_seen": time.time(),
                }
        self._fanout({"kind": "event", "topic": topic, "event": ev})

    def _apply_progress(self, ev):
        """One burndown point. Called with the lock held.

        The wire format is all strings; everything numeric is coerced here so
        the client never has to, and a malformed field costs one point rather
        than the whole request.
        """
        lot = ev.get("lot")
        if not lot:
            return

        def f(key, default=0.0):
            try:
                return float(ev.get(key, default))
            except (TypeError, ValueError):
                return default

        t = f("t")
        left = int(f("left"))
        state = ev.get("state") or "active"

        meta = self.lot_meta.get(lot)
        if meta is None:
            meta = self.lot_meta[lot] = {
                "cohort": ev.get("cohort") or "?",
                "part": ev.get("part") or "?",
                "release": f("rel"),
                "due": f("due"),
                "prio": f("prio"),
            }
        # Route length can grow: rework splices processed steps back onto the
        # front of the route, so this is a running maximum, not a constant.
        meta["route"] = max(int(meta.get("route", 0)), int(f("route")))
        meta["left"] = left
        meta["state"] = state
        meta["last_t"] = t
        meta["rem_s"] = f("rem_s")

        if self.sim_t is None or t > self.sim_t:
            self.sim_t = t

        self.burndown.append((lot, t, left, ev.get("reason") or "none",
                              f("wq"), f("wb"), f("wp"), f("rem_s")))

        # lot_meta would otherwise outlive the ring and grow without bound over
        # a long run. Trim completed lots first: an active lot's metadata is
        # still needed to draw its line.
        if len(self.lot_meta) > 6000:
            done = [k for k, m in self.lot_meta.items() if m.get("state") == "done"]
            for k in done[:2000]:
                self.lot_meta.pop(k, None)

    def burndown_view(self, cohorts=None, max_lots=400):
        """Group the ring into per-lot series. Takes the lock only to copy."""
        with self.lock:
            points = list(self.burndown)
            meta = {k: dict(v) for k, v in self.lot_meta.items()}
            sim_t = self.sim_t

        wanted = set(cohorts) if cohorts else None
        series = {}
        for lot, t, left, reason, wq, wb, wp, rem_s in points:
            m = meta.get(lot)
            if m is None:
                continue
            if wanted is not None and m["cohort"] not in wanted:
                continue
            s = series.get(lot)
            if s is None:
                if len(series) >= max_lots:
                    continue
                s = series[lot] = []
            s.append({"t": t, "left": left, "reason": reason,
                      "wq": wq, "wb": wb, "wp": wp, "rem_s": rem_s})
        return series, meta, sim_t

    def add_decision(self, d):
        with self.lock:
            self.decisions.append({**d, "ts": time.time()})
            tool = d.get("tool")
            if tool:
                s = self.tool_stats[tool]
                s["dispatches"] += 1
                s["lots"] += int(d.get("lots") or 0)
                s["queue"] = _as_int(d.get("queue"))
                setup = d.get("setup")
                if setup and s["setup"] is not None and setup != s["setup"]:
                    s["changeovers"] += 1
                s["setup"] = setup
                s["last_day"] = _as_float(d.get("day"))
                s["last_ts"] = time.time()
                self.tool_recent[tool].append({**d, "ts": s["last_ts"]})
        self._fanout({"kind": "decision", "decision": d})

    def _fanout(self, msg):
        dead = []
        for q in self.subscribers:
            try:
                q.put_nowait(msg)
            except queue.Full:
                dead.append(q)          # slow client: drop it, never block ingest
        for q in dead:
            try:
                self.subscribers.remove(q)
            except ValueError:
                pass

    def subscribe(self):
        q = queue.Queue(maxsize=200)
        self.subscribers.append(q)
        return q

    def unsubscribe(self, q):
        try:
            self.subscribers.remove(q)
        except ValueError:
            pass

    def snapshot(self):
        with self.lock:
            now = time.time()
            recent = [c for ts, c in self.throughput if now - ts < 120]
            rate = 0.0
            if len(recent) >= 2:
                span = 120.0
                rate = (recent[-1] - recent[0]) / span * 3600  # lots/hour
            return {
                "ts": datetime.now(timezone.utc).isoformat(),
                "counts": dict(self.counts),
                "tools": {k: dict(v) for k, v in self.tools.items()},
                "ready": len(self.lots_ready),
                "in_flight": len(self.in_flight),
                "completed": self.counts.get("LOT_COMPLETE", 0),
                "throughput_lots_per_hour": round(rate, 1),
                "decisions_seen": len(self.decisions),
            }


mirror = FabMirror()


def parse_envelope(payload: str) -> dict:
    """Decode the key=value wire format from events.hpp."""
    out = {}
    for tok in payload.split(";"):
        if "=" in tok:
            k, v = tok.split("=", 1)
            out[k] = v
    return out


def bootstrap_from_state(Consumer):
    """Rebuild WIP from the compacted state topics before tailing events.

    Without this the mirror starts empty and learns a lot exists only when that
    lot next moves. Lots sitting in a long queue stay invisible for hours of
    simulated time, and the lots loaded from WIP.txt are never released at all,
    so they never announce themselves. The dashboard then under-reports WIP for
    as long as it takes every lot to touch a tool.

    Reading these topics from the beginning is cheap precisely because they are
    compacted: the log holds one record per live key, so "from the beginning"
    is the current fab, not its history. This is what the compaction setting in
    create-topics.sh was for.
    """
    from confluent_kafka import TopicPartition, OFFSET_BEGINNING

    topics = ["fab.lot.state", "fab.tool.state"]
    c = Consumer({
        "bootstrap.servers": KAFKA_BROKERS,
        # A fresh group every time: this is a rebuild, not a resumable read, so
        # it must not inherit a committed offset from a previous process.
        "group.id": f"fab-api-bootstrap-{os.getpid()}",
        "auto.offset.reset": "earliest",
        "enable.auto.commit": False,
    })

    # assign(), not subscribe(). A subscribing consumer returns None for
    # several seconds while the group rebalances, which is indistinguishable
    # from "topic is empty" -- the first version of this read 0 records from a
    # topic holding 3,451 of them. Explicit assignment starts reading
    # immediately and lets us stop at a known end rather than at a guess.
    parts, ends = [], {}
    md = c.list_topics(timeout=10)
    for t in topics:
        tmd = md.topics.get(t)
        if tmd is None or tmd.error is not None:
            continue
        for pid in tmd.partitions:
            tp = TopicPartition(t, pid, OFFSET_BEGINNING)
            parts.append(tp)
            _lo, hi = c.get_watermark_offsets(TopicPartition(t, pid), timeout=10)
            ends[(t, pid)] = hi
    if not parts:
        print("[bootstrap] no state topics; starting cold", file=sys.stderr, flush=True)
        c.close()
        return
    c.assign(parts)

    remaining = sum(ends.values())
    lots = tools = 0
    idle = 0
    try:
        # Read to the high watermark captured above -- a definite end, so this
        # cannot hang behind a live producer still appending.
        while remaining > 0 and idle < 20:
            msg = c.poll(0.5)
            if msg is None or msg.error():
                idle += 1
                continue
            idle = 0
            remaining -= 1
            ev = parse_envelope(msg.value().decode("utf-8", "replace"))
            if msg.topic() == "fab.lot.state":
                lot = ev.get("lot")
                if not lot:
                    continue
                tool = ev.get("tool")
                with mirror.lock:
                    # A snapshot record is authoritative: it replaces whatever
                    # the mirror believed, in both directions.
                    mirror.lots_ready.pop(lot, None)
                    mirror.in_flight.pop(lot, None)
                    if tool:
                        mirror.in_flight[lot] = tool
                    else:
                        mirror.lots_ready[lot] = ev
                lots += 1
            else:
                tool = ev.get("tool")
                if tool:
                    with mirror.lock:
                        mirror.tools[tool] = {
                            "online": ev.get("online") != "0",
                            "last_seen": time.time(),
                        }
                    tools += 1
    except Exception as e:
        print(f"[bootstrap] {e!r}", file=sys.stderr, flush=True)
    finally:
        c.close()
    print(f"[bootstrap] {lots} lots, {tools} tools from compacted state",
          file=sys.stderr, flush=True)


def kafka_consumer_loop():
    """Consume-only. This function never produces."""
    try:
        from confluent_kafka import Consumer
    except ImportError:
        app.logger.warning("confluent_kafka missing; running with no live feed")
        return

    c = Consumer({
        "bootstrap.servers": KAFKA_BROKERS,
        "group.id": f"fab-api-mirror-{os.getpid()}",
        "auto.offset.reset": "latest",
        "enable.auto.commit": True,      # a mirror may lose its place safely
    })
    bootstrap_from_state(Consumer)
    c.subscribe(["fab.lot.events", "fab.tool.events", "fab.dispatch.decisions"])
    app.logger.info("kafka mirror attached to %s", KAFKA_BROKERS)

    while True:
        msg = c.poll(1.0)
        if msg is None or msg.error():
            continue
        payload = msg.value().decode("utf-8", "replace")
        topic = msg.topic()
        if topic == "fab.dispatch.decisions":
            mirror.add_decision(parse_envelope(payload))
        else:
            mirror.apply(topic, parse_envelope(payload))


def feed_file_loop():
    """Consume-only tail of a JSONL event file. Never produces.

    Each line is {"topic": ..., "payload": "k=v;k=v"} -- the same wire format
    the Kafka path decodes, so the mirror cannot tell the two apart and no
    parsing logic is duplicated. This exists so the dashboard can run end to
    end without a broker; it is not a substitute for Kafka in the data zone.

    Follows the file the way `tail -F` does: waits for it to appear, and keeps
    reading past EOF so a producer can start after the API.
    """
    app.logger.info("file mirror attached to %s", FEED_FILE)
    pos = 0
    while True:
        try:
            if not os.path.exists(FEED_FILE):
                time.sleep(1.0)
                continue
            with open(FEED_FILE, "r") as f:
                f.seek(pos)
                # readline(), not `for line in f`: iterating a file object
                # disables tell() ("telling position disabled by next() call"),
                # and we need tell() to resume where we stopped.
                while True:
                    line = f.readline()
                    if not line:
                        break            # caught up; wait for more
                    if not line.endswith("\n"):
                        break            # partial write; re-read it next pass
                    pos = f.tell()
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rec = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    topic = rec.get("topic", "")
                    ev = parse_envelope(rec.get("payload", ""))
                    if topic == "fab.dispatch.decisions":
                        mirror.add_decision(ev)
                    elif topic:
                        mirror.apply(topic, ev)
                if os.path.getsize(FEED_FILE) < pos:
                    pos = 0              # truncated / new run, start over
        except Exception as e:            # a broken feed must not kill the API
            # Print as well as log: a silently-retrying feed loop looks
            # identical to "no data yet", which cost real debugging time once.
            print(f"[feed] error: {e!r}", file=sys.stderr, flush=True)
            time.sleep(1.0)
        time.sleep(0.2)


# ---------------------------------------------------------------------------
# Read-only guard. Belt and braces with the nginx limit_except.
# ---------------------------------------------------------------------------

SCENARIO_PATHS = {"/api/scenario", "/api/scenario/compare", "/api/chat",
                  # Playback pacing of the simulator feed. It reaches a
                  # replay process, not the dispatcher, and changes only
                  # when events are emitted -- never fab state.
                  "/api/sim/control"}


@app.before_request
def enforce_read_only():
    if not READ_ONLY:
        return None
    if request.method in ("GET", "HEAD", "OPTIONS"):
        return None
    # Scenario and sim-playback POSTs are permitted: they run against a cloned
    # registry / a replay process and have no path back to the dispatcher.
    # Everything else is refused.
    if request.path in SCENARIO_PATHS and request.method == "POST":
        return None
    return jsonify({"error": "read-only zone boundary: writes are not permitted"}), 403


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@app.get("/health")
def health():
    return jsonify({"ok": True, "read_only": READ_ONLY, "zone": "boundary-2-3"})


@app.get("/api/zones")
def zones():
    """Serves the segmentation policy so the UI can render the real topology."""
    path = os.getenv("ZONES_FILE", "/etc/fab/zones.yaml")
    try:
        import yaml
        with open(path) as f:
            return jsonify(yaml.safe_load(f))
    except Exception as e:
        return jsonify({"error": str(e)}), 404


@app.get("/api/state")
def state():
    return jsonify(mirror.snapshot())


# ---------------------------------------------------------------------------
# Floorplan. Geometry is static and cached; state streams separately.
# ---------------------------------------------------------------------------

FLOORPLAN_FILE = os.getenv(
    "FLOORPLAN_FILE",
    os.path.join(os.path.dirname(os.path.abspath(__file__)),
                 "..", "config", "floorplan.json"))

# Simulator family prefix -> floorplan zone. Longest prefix wins, so
# LithoTrack/LithoMet resolve before Litho. Delay_* is deliberately absent:
# those are queue-time placeholders, not equipment, and putting them on a floor
# map would invent 400 machines that do not exist.
FAMILY_ZONE = [
    ("LithoTrack", "LIT"), ("LithoMet", "LIT"), ("Litho", "LIT"),
    ("Dielectric", "TF"), ("TF", "TF"),
    ("Diffusion", "DIF"), ("EPI", "DIF"),
    ("Planar", "CMP"),
    ("Implant", "IMP"),
    ("DefMet", "MET"), ("DefMEt", "MET"),
    ("WE", "CLN"),
    ("DE", "ETC"),
]
_FAMILY_ZONE_SORTED = sorted(FAMILY_ZONE, key=lambda kv: -len(kv[0]))


def is_delay_group(group):
    return group.lower().startswith("delay")


def zone_for_group(group):
    # Checked first and explicitly: "Delay" starts with "DE", so without this
    # every queue-time placeholder silently lands in the dry-etch bays and the
    # map shows 400 machines that do not exist.
    if is_delay_group(group):
        return None
    for prefix, zone in _FAMILY_ZONE_SORTED:
        if group.lower().startswith(prefix.lower()):
            return zone
    return None


class Floorplan:
    """Geometry plus a stable tool -> cell assignment.

    The assignment is derived, because SMT2020 has no floorplan, but it must be
    deterministic: tools are sorted and dealt round-robin into their zone's
    cells, so a tool keeps its cell across restarts. A map that reshuffles on
    reload teaches an operator nothing.
    """

    def __init__(self):
        self.doc = None
        self.error = None
        self.zone_cells = {}
        self.assign = {}          # tool_id -> (bay, seg)
        self.cell_tools = {}      # (bay, seg) -> [tool_id]
        self._assigned_for = set()
        self.lock = threading.Lock()
        try:
            with open(os.path.abspath(FLOORPLAN_FILE)) as f:
                self.doc = json.load(f)
            for z in self.doc["zones"]:
                self.zone_cells[z["id"]] = [tuple(c) for c in z["cells"]]
        except Exception as e:
            self.error = str(e)

    def reassign(self, tool_ids):
        """Recompute placement when the tool set changes. Idempotent."""
        if self.doc is None:
            return
        with self.lock:
            ids = set(tool_ids)
            if ids == self._assigned_for:
                return
            self._assigned_for = ids
            assign, cell_tools = {}, {}
            by_zone = defaultdict(list)
            for t in sorted(ids):
                z = zone_for_group(tool_group(t))
                if z:
                    by_zone[z].append(t)
            for z, tools in by_zone.items():
                cells = self.zone_cells.get(z) or []
                if not cells:
                    continue
                for i, t in enumerate(tools):
                    cell = cells[i % len(cells)]
                    assign[t] = cell
                    cell_tools.setdefault(cell, []).append(t)
            self.assign, self.cell_tools = assign, cell_tools


floorplan = Floorplan()


def _tool_row(tool_id):
    """One tool's public shape. Kept in one place so the index and the detail
    view cannot disagree about what a tool looks like."""
    with mirror.lock:
        meta = mirror.tools.get(tool_id, {})
        s = dict(mirror.tool_stats.get(tool_id, {}))
        running = [l for l, t in mirror.in_flight.items() if t == tool_id]
    return {
        "id": tool_id,
        "group": tool_group(tool_id),
        "online": meta.get("online", True),
        "last_seen": meta.get("last_seen"),
        "dispatches": s.get("dispatches", 0),
        "lots": s.get("lots", 0),
        "started": s.get("started", 0),
        "completed": s.get("completed", 0),
        "queue": s.get("queue"),
        "setup": s.get("setup"),
        "changeovers": s.get("changeovers", 0),
        "last_day": s.get("last_day"),
        "last_ts": s.get("last_ts"),
        "running": running[:25],
        "running_count": len(running),
    }


def _known_tools():
    with mirror.lock:
        return set(mirror.tools) | set(mirror.tool_stats)


@app.get("/api/layout")
def layout():
    """Geometry + placement. Static enough to cache; state is a separate call.

    Delay_* tools are returned in their own section rather than as cells. They
    are queue-time placeholders with no physical location, and giving them a
    bay would assert a position the dataset does not have.
    """
    if floorplan.doc is None:
        return jsonify({"error": f"floorplan unavailable: {floorplan.error}"}), 500

    ids = _known_tools()
    floorplan.reassign(ids)

    with floorplan.lock:
        assign = {t: list(c) for t, c in floorplan.assign.items()}
        cell_tools = {f"{b},{s}": list(v) for (b, s), v in floorplan.cell_tools.items()}

    delay_groups = defaultdict(list)
    unplaced = []
    for t in sorted(ids):
        g = tool_group(t)
        if is_delay_group(g):
            delay_groups[g].append(t)
        elif t not in assign:
            unplaced.append(t)

    doc = dict(floorplan.doc)
    doc["assign"] = assign
    doc["cell_tools"] = cell_tools
    doc["delays"] = [{"group": g, "tools": v, "count": len(v)}
                     for g, v in sorted(delay_groups.items())]
    # Surfaced rather than silently dropped: a tool with no zone mapping is a
    # gap in FAMILY_ZONE, and hiding it would make the map quietly incomplete.
    doc["unplaced"] = unplaced
    doc["placed"] = len(assign)
    return jsonify(doc)


@app.get("/api/layout/state")
def layout_state():
    """Per-cell and per-delay-group rollup. This is the layer that ticks."""
    ids = _known_tools()
    floorplan.reassign(ids)

    with floorplan.lock:
        cell_tools = {k: list(v) for k, v in floorplan.cell_tools.items()}

    cells = []
    for (bay, seg), tools in cell_tools.items():
        rows = [_tool_row(t) for t in tools]
        queues = [r["queue"] for r in rows if r["queue"] is not None]
        cells.append({
            "bay": bay, "seg": seg,
            "tools": len(rows),
            "down": sum(1 for r in rows if not r["online"]),
            "running": sum(r["running_count"] for r in rows),
            "dispatches": sum(r["dispatches"] for r in rows),
            "wip": sum(queues) if queues else 0,
            "queue_max": max(queues) if queues else None,
        })

    delays = []
    dgroups = defaultdict(list)
    for t in ids:
        g = tool_group(t)
        if is_delay_group(g):
            dgroups[g].append(t)
    for g, tools in sorted(dgroups.items()):
        rows = [_tool_row(t) for t in tools]
        queues = [r["queue"] for r in rows if r["queue"] is not None]
        delays.append({
            "group": g,
            "tools": len(rows),
            "running": sum(r["running_count"] for r in rows),
            "dispatches": sum(r["dispatches"] for r in rows),
            "wip": sum(queues) if queues else 0,
        })

    return jsonify({"ts": time.time(), "cells": cells, "delays": delays})


@app.get("/api/tools")
def tools_index():
    """Every tool we have heard of, grouped by type.

    Union of the status feed and the decision feed: a tool that has announced
    itself but never dispatched still belongs on the index, and so does one
    that dispatched before its status arrived.
    """
    with mirror.lock:
        ids = set(mirror.tools) | set(mirror.tool_stats)
    rows = [_tool_row(t) for t in sorted(ids)]

    groups = {}
    for r in rows:
        g = groups.setdefault(r["group"], {
            "group": r["group"], "tools": [],
            "count": 0, "offline": 0, "dispatches": 0, "lots": 0,
            "queue_max": None,
        })
        g["tools"].append(r)
        g["count"] += 1
        g["offline"] += 0 if r["online"] else 1
        g["dispatches"] += r["dispatches"]
        g["lots"] += r["lots"]
        if r["queue"] is not None:
            g["queue_max"] = max(g["queue_max"] or 0, r["queue"])

    # Busiest group first: the index should answer "where is the constraint"
    # before it answers "what exists".
    ordered = sorted(groups.values(),
                     key=lambda g: (g["dispatches"], g["count"]), reverse=True)
    return jsonify({"groups": ordered, "total": len(rows)})


@app.get("/api/tools/<path:tool_id>")
def tool_detail(tool_id):
    with mirror.lock:
        known = tool_id in mirror.tools or tool_id in mirror.tool_stats
        recent = list(reversed(mirror.tool_recent.get(tool_id, ())))
        events = [e for e in reversed(mirror.events)
                  if e.get("tool") == tool_id][:40]
    if not known:
        return jsonify({"error": f"unknown tool {tool_id}"}), 404
    row = _tool_row(tool_id)
    row["recent_decisions"] = recent
    row["recent_events"] = events
    return jsonify(row)


@app.get("/api/events")
def events():
    n = min(int(request.args.get("limit", 100)), 500)
    with mirror.lock:
        return jsonify(list(mirror.events)[-n:])


@app.get("/api/decisions")
def decisions():
    n = min(int(request.args.get("limit", 100)), 500)
    with mirror.lock:
        return jsonify(list(mirror.decisions)[-n:])


def _cohort_rows(meta):
    """One row per cohort, ranked by how recently it moved."""
    by = {}
    for lot, m in meta.items():
        c = by.setdefault(m["cohort"], {
            "cohort": m["cohort"], "part": m["part"], "lots": 0, "done": 0,
            "left": [], "last_t": 0.0, "release": m["release"], "due": m["due"],
        })
        c["lots"] += 1
        c["done"] += 1 if m.get("state") == "done" else 0
        c["left"].append(m.get("left", 0))
        c["last_t"] = max(c["last_t"], m.get("last_t", 0.0))
        c["release"] = min(c["release"], m["release"])
        c["due"] = max(c["due"], m["due"])

    rows = []
    for c in by.values():
        left = sorted(c.pop("left"))
        n = len(left)
        # Spread is the point of the envelope: a widening band means the cohort
        # is desynchronising and will stall at the next batch step.
        c["min_left"] = left[0]
        c["max_left"] = left[-1]
        c["med_left"] = left[n // 2]
        c["spread"] = left[-1] - left[0]
        rows.append(c)
    rows.sort(key=lambda r: (-r["last_t"], r["cohort"]))
    return rows


@app.get("/api/lots")
def lots_index():
    """Cohort index for the burndown view.

    Returns every cohort currently in the ring, ranked by last movement. The
    client picks which to expand; series come from /api/lots/<cohort> so the
    index stays small even with hundreds of cohorts in a long run.
    """
    _, meta, sim_t = mirror.burndown_view(cohorts=[])
    rows = _cohort_rows(meta)
    return jsonify({
        "now_t": sim_t,
        "cohorts": rows[:int(request.args.get("limit", 60))],
        "total_cohorts": len(rows),
        "lots_tracked": len(meta),
        "points_held": len(mirror.burndown),
        "points_cap": BURNDOWN_MAX,
    })


@app.get("/api/lots/<path:cohort>")
def lots_cohort(cohort):
    """Per-lot burndown series for one cohort.

    `steps_remaining` is computed upstream from the route and passed through
    untouched. It is not monotonic -- rework splices processed steps back onto
    the route -- and nothing here clamps it, because an upward jog is the most
    informative thing this chart shows.
    """
    series, meta, sim_t = mirror.burndown_view(cohorts=[cohort])
    if not series:
        return jsonify({"cohort": cohort, "now_t": sim_t, "lots": [],
                        "note": "no points held for this cohort"}), 200

    lots = []
    for lot, pts in series.items():
        m = meta[lot]
        pts.sort(key=lambda x: x["t"])
        lots.append({
            "lot": lot,
            "part": m["part"],
            "route": m.get("route", 0),
            "release": m["release"],
            "due": m["due"],
            "prio": m["prio"],
            "state": m.get("state", "active"),
            "points": pts,
        })
    lots.sort(key=lambda r: r["lot"])
    return jsonify({"cohort": cohort, "now_t": sim_t, "lots": lots})


@app.get("/api/stream")
def stream():
    """Server-Sent Events: live feed for the dashboard."""
    def gen():
        q = mirror.subscribe()
        try:
            yield f"data: {json.dumps({'kind':'hello'})}\n\n"
            last_beat = time.time()
            while True:
                try:
                    msg = q.get(timeout=5)
                    yield f"data: {json.dumps(msg)}\n\n"
                except queue.Empty:
                    pass
                if time.time() - last_beat > 5:
                    snap = mirror.snapshot()
                    yield f"data: {json.dumps({'kind':'state','state':snap})}\n\n"
                    last_beat = time.time()
        finally:
            mirror.unsubscribe(q)

    return Response(gen(), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache",
                             "X-Accel-Buffering": "no"})


def lots_from_live_pool(limit=200):
    """The live ready pool as scenario lots. Empty list if nothing is flowing."""
    with mirror.lock:
        return [
            {
                "lot_id": lid,
                "product_id": ev.get("prod", ""),
                "recipe": ev.get("recipe", ""),
                "reticle": ev.get("reticle", ""),
                "wafer_count": int(ev.get("wafers", 25) or 25),
                "priority": float(ev.get("prio", 1.0) or 1.0),
                "qtime_slack_s": float(ev.get("slack", 3600.0) or 3600.0),
            }
            for lid, ev in list(mirror.lots_ready.items())[:limit]
        ]


def demo_lots(n=24):
    """A deterministic stand-in pool for local dev, off unless DEMO_LOTS=1.

    Recipes come from the tool config's active_recipes, so the lots are
    actually eligible for real tools -- a demo pool that assigns nothing would
    look identical to a broken solver. Kept deliberately behind a flag: with a
    live feed attached, an empty pool is a real condition and inventing lots
    would hide it.
    """
    try:
        with open(TOOL_CONFIG) as f:
            cfg = json.load(f)
        recipes = cfg.get("active_recipes") or []
    except Exception:
        recipes = []
    if not recipes:
        return []
    out = []
    for i in range(n):
        recipe = recipes[i % len(recipes)]
        out.append({
            "lot_id": f"DEMO_{i:03d}",
            "product_id": f"P{i % 3}",
            "recipe": recipe,
            # Only the expose steps carry a reticle; that is what exercises
            # the exclusivity constraint.
            "reticle": f"RET_{recipe}" if "EXPOSE" in recipe else "",
            "wafer_count": 25,
            "priority": 1.0 + (i % 4),
            "qtime_slack_s": 900.0 * (1 + i % 5),
        })
    return out


def resolve_lots(body):
    """Explicit lots, else the live pool, else the demo pool if enabled.

    Both scenario endpoints go through this. /api/scenario/compare used to
    lack the fallback entirely, so the dashboard -- which posts only
    tool_overrides -- got a 500 on every click.
    """
    if body.get("lots"):
        return body["lots"], None
    lots = lots_from_live_pool()
    if lots:
        return lots, None
    if DEMO_LOTS:
        return demo_lots(), "demo"
    return [], None


@app.post("/api/scenario")
def scenario():
    """
    What-if. Shells out to the C++ planner against a CLONED registry.

    We do NOT reimplement dispatch logic here. A scenario answer that diverges
    from what the dispatcher would actually do is worse than no answer.
    """
    body = request.get_json(silent=True) or {}

    body["lots"], _src = resolve_lots(body)
    if not body["lots"]:
        return jsonify({"error": "no lots supplied and live ready pool is empty"}), 400

    try:
        proc = subprocess.run(
            [SCENARIO_BIN, "--config", TOOL_CONFIG, "--solver", SOLVER],
            input=json.dumps(body), capture_output=True, text=True, timeout=30,
        )
    except subprocess.TimeoutExpired:
        return jsonify({"error": "scenario solve exceeded 30s"}), 504
    except FileNotFoundError:
        return jsonify({"error": f"scenario binary missing at {SCENARIO_BIN}"}), 500

    if proc.returncode != 0:
        return jsonify({"error": "scenario failed",
                        "detail": proc.stderr[:500] or proc.stdout[:500]}), 500
    try:
        return jsonify(json.loads(proc.stdout))
    except json.JSONDecodeError:
        return jsonify({"error": "unparseable scenario output",
                        "raw": proc.stdout[:500]}), 500


@app.post("/api/scenario/compare")
def scenario_compare():
    """Run baseline vs. what-if and diff them. This is the useful endpoint."""
    body = request.get_json(silent=True) or {}
    overrides = body.pop("tool_overrides", [])

    body["lots"], lot_source = resolve_lots(body)
    if not body["lots"]:
        return jsonify({"error": "no lots supplied and live ready pool is empty. "
                                 "Start a feed, or set DEMO_LOTS=1 for a local "
                                 "stand-in pool."}), 400

    def run(payload):
        proc = subprocess.run(
            [SCENARIO_BIN, "--config", TOOL_CONFIG, "--solver", SOLVER],
            input=json.dumps(payload), capture_output=True, text=True, timeout=30)
        return json.loads(proc.stdout) if proc.returncode == 0 else None

    base_body = dict(body, tool_overrides=[])
    what_body = dict(body, tool_overrides=overrides)

    base = run(base_body)
    what = run(what_body)
    if base is None or what is None:
        return jsonify({"error": "one or both scenario runs failed"}), 500

    base_map = {a["lot_id"]: a["tool"] for a in base["assignments"]}
    what_map = {a["lot_id"]: a["tool"] for a in what["assignments"]}

    rerouted = [{"lot_id": k, "from": base_map[k], "to": what_map[k]}
                for k in base_map if k in what_map and base_map[k] != what_map[k]]
    dropped  = [k for k in base_map if k not in what_map]

    return jsonify({
        "baseline": base,
        "scenario": what,
        "diff": {
            "assigned_delta": what["assigned"] - base["assigned"],
            "objective_delta": round(what["objective"] - base["objective"], 2),
            "rerouted": rerouted,
            "newly_unassigned": dropped,
        },
    })


# ---------------------------------------------------------------------------
# Assistant — Claude on Vertex AI, grounded in the tools below.
# ---------------------------------------------------------------------------

def scenario_runner(tool_overrides):
    """Shared by /api/scenario/compare and the assistant's run_scenario tool."""
    with mirror.lock:
        lots = [
            {
                "lot_id": lid,
                "product_id": ev.get("prod", ""),
                "recipe": ev.get("recipe", ""),
                "reticle": ev.get("reticle", ""),
                "wafer_count": int(ev.get("wafers", 25) or 25),
                "priority": float(ev.get("prio", 1.0) or 1.0),
                "qtime_slack_s": float(ev.get("slack", 3600.0) or 3600.0),
            }
            for lid, ev in list(mirror.lots_ready.items())[:200]
        ]
    if not lots:
        return {"error": "live ready pool is empty; no lots to plan"}

    def run(overrides):
        proc = subprocess.run(
            [SCENARIO_BIN, "--config", TOOL_CONFIG, "--solver", SOLVER],
            input=json.dumps({"lots": lots, "tool_overrides": overrides}),
            capture_output=True, text=True, timeout=30)
        return json.loads(proc.stdout) if proc.returncode == 0 else None

    try:
        base = run([])
        what = run(tool_overrides) if tool_overrides else base
    except Exception as e:                                    # noqa: BLE001
        return {"error": f"scenario failed: {e}"}
    if base is None or what is None:
        return {"error": "scenario solve failed"}

    bm = {a["lot_id"]: a["tool"] for a in base["assignments"]}
    wm = {a["lot_id"]: a["tool"] for a in what["assignments"]}
    return {
        "baseline": base, "scenario": what,
        "diff": {
            "assigned_delta": what["assigned"] - base["assigned"],
            "objective_delta": round(what["objective"] - base["objective"], 2),
            "rerouted": [{"lot_id": k, "from": bm[k], "to": wm[k]}
                         for k in bm if k in wm and bm[k] != wm[k]],
            "newly_unassigned": [k for k in bm if k not in wm],
        },
    }


assistant = FabAssistant(mirror, scenario_runner)


@app.get("/api/sim/control")
def sim_control_get():
    """Current playback pacing of the simulator feed.

    `available` is false when no feed has ever written the file -- a live
    Kafka feed from somewhere else has no speed to report, and the dashboard
    shows the badge without a menu rather than pretending it can steer it.
    """
    try:
        with open(SIM_CONTROL_FILE) as f:
            ctl = json.load(f)
    except (OSError, ValueError):
        return jsonify({"available": False, "speeds": SIM_SPEEDS})
    return jsonify({
        "available": True,
        "speed": ctl.get("speed"),
        "paused": bool(ctl.get("paused")),
        "updated": ctl.get("updated"),
        "speeds": SIM_SPEEDS,
    })


@app.post("/api/sim/control")
def sim_control_set():
    """Set playback speed and/or pause. Both fields are optional."""
    body = request.get_json(silent=True) or {}
    try:
        with open(SIM_CONTROL_FILE) as f:
            ctl = json.load(f)
    except (OSError, ValueError):
        ctl = {"speed": None, "paused": False}

    if "speed" in body:
        try:
            speed = float(body["speed"])
        except (TypeError, ValueError):
            return jsonify({"error": "speed must be a number"}), 400
        # Upper bound is the menu's top rate: an arbitrary multiplier from a
        # POST can outrun the browser's ability to render the stream.
        if not 0 < speed <= max(SIM_SPEEDS):
            return jsonify({"error": f"speed must be in (0, {max(SIM_SPEEDS)}]"}), 400
        ctl["speed"] = speed
    if "paused" in body:
        ctl["paused"] = bool(body["paused"])
    ctl["updated"] = time.time()
    ctl["source"] = "api"

    try:
        os.makedirs(os.path.dirname(os.path.abspath(SIM_CONTROL_FILE)), exist_ok=True)
        tmp = SIM_CONTROL_FILE + ".tmp"
        with open(tmp, "w") as f:
            json.dump(ctl, f)
        os.replace(tmp, SIM_CONTROL_FILE)   # atomic: the feed polls this file
    except OSError as e:
        return jsonify({"error": f"cannot write control file: {e}"}), 500
    return jsonify({"available": True, "speed": ctl.get("speed"),
                    "paused": bool(ctl.get("paused")), "speeds": SIM_SPEEDS})


@app.get("/api/chat/status")
def chat_status():
    return jsonify({"available": assistant.available, "error": assistant.error,
                    "model": os.getenv("VERTEX_MODEL", "claude-sonnet-4-5@20250929")})


@app.post("/api/chat")
def chat():
    """
    Grounded Q&A over live state and scenarios.

    The assistant reads state and simulates; it cannot change the fab. Every
    figure in a reply comes from a tool result, never from model recall.
    """
    body = request.get_json(silent=True) or {}
    msgs = body.get("messages") or []
    if not msgs:
        return jsonify({"error": "no messages"}), 400
    if len(msgs) > 40:
        msgs = msgs[-40:]                  # bound context growth

    result = assistant.ask(msgs)
    if result["error"] and not result["reply"]:
        return jsonify(result), 503
    return jsonify(result)


def start_feeds():
    """FEED_FILE replaces Kafka rather than supplementing it: running both
    would interleave two sources into one mirror and make the state
    unattributable."""
    if FEED_FILE:
        threading.Thread(target=feed_file_loop, daemon=True).start()
    else:
        threading.Thread(target=kafka_consumer_loop, daemon=True).start()


if __name__ == "__main__":
    start_feeds()
    # PORT so a test run can stand beside a dev API instead of fighting it for
    # :8000. The container path pins 8000 and does not set this.
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "8000")), threaded=True)
else:
    start_feeds()
