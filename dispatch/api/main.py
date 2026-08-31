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
from openapi import register_docs

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
# Tool recovery watchdog. TOOL_STATUS is the only thing that marks a tool
# down, so a lost, filtered, or never-emitted online=1 leaves it down forever
# and the roster decays to all-down -- which is what it did before the feed
# learned to emit recoveries. Two cheap guards, neither of which invents a
# state the fab has not shown us:
#   * activity beats status. A tool that starts a lot is running, whatever its
#     last status said, so we mark it back up.
#   * nothing stays down forever. Past TOOL_DOWN_TTL_S of wall clock with no
#     word either way we assume up and say so in the row.
# Both are deliberately blunt. This is a dashboard, not the MES.
TOOL_DOWN_TTL_S = float(os.getenv("TOOL_DOWN_TTL_S", "900"))
# Availability ring for the tool-index sparkline. One point per sample, so
# 5s x 2880 is four hours of history at a fixed, tiny memory cost.
AVAIL_SAMPLE_S  = float(os.getenv("AVAIL_SAMPLE_S", "5"))
AVAIL_MAX       = int(os.getenv("AVAIL_MAX", "2880"))

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
        # Timeline provenance: which run and simulated day the snapshot came
        # from, and which the live stream is on. See note_timeline().
        self.snapshot_run = None
        self.snapshot_day = None
        self.stream_run = None
        self.stream_day = None
        # Cohort burndown. `burndown` holds compact points; `lot_meta` holds the
        # per-lot constants (cohort, route length, due date) that would
        # otherwise be repeated on every point.
        self.burndown = deque(maxlen=BURNDOWN_MAX)
        self.lot_meta = {}
        self.burndown_run = None   # which simulation run the ring belongs to
        # Where warm-up ends and the live stream begins. Published by the feed
        # rather than inferred, because the chart draws the two sides
        # differently and a guessed boundary would mislabel real history.
        self.warm_t = None
        self.sim_t = None        # latest simulated time seen, for the "now" rule
        self.sim_t_at = None     # wall clock when that reading arrived
        # tool_id -> wall clock when it was last marked down, for the watchdog.
        self.down_since = {}
        # tool_id -> why it came back ("status" | "activity" | "watchdog"),
        # so a restored tool is auditable rather than just quietly online.
        self.recovered_by = {}
        # (ts, online, total) samples for the tool-index availability chart.
        self.availability = deque(maxlen=AVAIL_MAX)

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
                    # A tool that just started a lot is up, whatever the last
                    # status event claimed.
                    self._mark_up(ev["tool"], "activity")
            elif t == "LOT_COMPLETE":
                # Credit the completion to whichever tool was running it, which
                # only in_flight knows -- the event itself carries no tool.
                tool = self.in_flight.pop(ev.get("lot"), None)
                if tool:
                    self.tool_stats[tool]["completed"] += 1
                self.throughput.append((time.time(), self.counts["LOT_COMPLETE"]))
            elif t == "LOT_PROGRESS":
                self._apply_progress(ev)
            elif t == "LOT_STATE":
                self._apply_lot_state(ev)
            elif t == "TOOL_STATUS":
                tool = ev.get("tool")
                online = ev.get("online") != "0"
                self.tools[tool] = {
                    "online": online,
                    "last_seen": time.time(),
                    "reason": ev.get("reason"),
                    # Advertised outage length, when the feed knows it. Lets
                    # the UI say "back in ~40 min" instead of just "down".
                    "down_s": _as_float(ev.get("down_s")),
                }
                if online:
                    self._mark_up(tool, "status")
                else:
                    self.down_since.setdefault(tool, time.time())
                    self.recovered_by.pop(tool, None)
        self._fanout({"kind": "event", "topic": topic, "event": ev})

    def _mark_up(self, tool, how):
        """Put a tool back online. Called with the lock held.

        Idempotent, and a no-op for a tool that was never down, so the ordinary
        path (every LOT_STARTED) costs one dict lookup.
        """
        if not tool:
            return
        was_down = tool in self.down_since or \
            not self.tools.get(tool, {}).get("online", True)
        if not was_down:
            return
        self.down_since.pop(tool, None)
        meta = self.tools.setdefault(tool, {})
        meta["online"] = True
        meta["last_seen"] = time.time()
        meta.pop("reason", None)
        meta.pop("down_s", None)
        # "status" is the fab telling us; the other two are us inferring it.
        # Only the inferences are worth flagging in the UI.
        if how == "status":
            self.recovered_by.pop(tool, None)
        else:
            self.recovered_by[tool] = how

    def sweep_and_sample(self):
        """Watchdog tick: restore anything stuck down, then record a point.

        One thread does both because they read the same state, and doing them
        together means the chart never shows a fab that the sweep is about to
        change.
        """
        now = time.time()
        with self.lock:
            for tool, since in list(self.down_since.items()):
                if now - since >= TOOL_DOWN_TTL_S:
                    self._mark_up(tool, "watchdog")
            ids = set(self.tools) | set(self.tool_stats)
            total = len(ids)
            online = sum(1 for t in ids
                         if self.tools.get(t, {}).get("online", True))
            if total:
                self.availability.append((now, online, total))
            return online, total

    def _apply_lot_state(self, ev):
        """A snapshot record: the lot's position, and how it got there.

        The warm-up history rides on this topic because it is compacted -- one
        record per lot, so an API starting up long after the feed still rebuilds
        every active lot's past. Called with the lock held.
        """
        lot = ev.get("lot")
        if not lot:
            return

        run = ev.get("run")
        if run and run != self.burndown_run:
            self.burndown_run = run
            self.burndown.clear()
            self.lot_meta.clear()
            self.sim_t = None
            self.sim_t_at = None

        try:
            self.warm_t = float(ev.get("warm_t")) if ev.get("warm_t") else self.warm_t
        except (TypeError, ValueError):
            pass

        raw = ev.get("hist") or ""
        history = []
        for chunk in raw.split(","):
            if not chunk:
                continue
            t, _, v = chunk.partition(":")
            try:
                history.append({"t": float(t), "left": int(v)})
            except ValueError:
                continue

        m = self.lot_meta.setdefault(lot, {})
        m.setdefault("part", ev.get("part") or ev.get("product") or "?")
        m.setdefault("cohort", ev.get("cohort") or "?")
        m.setdefault("release", history[0]["t"] if history else 0.0)
        m.setdefault("due", 0.0)
        m.setdefault("prio", 0.0)
        m.setdefault("hot", False)
        if history:
            m["history"] = history
            # Seed position from the snapshot so a lot that has not moved since
            # warm-up still has a length and a place on the chart. A later
            # LOT_PROGRESS overwrites all of this with live truth.
            m.setdefault("left", history[-1]["left"])
            m.setdefault("last_t", history[-1]["t"])
            try:
                done = int(float(ev.get("done_steps") or 0))
                m.setdefault("route", done + history[-1]["left"])
            except (TypeError, ValueError):
                pass
        m.setdefault("state", "active")

    def _apply_progress(self, ev):
        """One burndown point. Called with the lock held.

        The wire format is all strings; everything numeric is coerced here so
        the client never has to, and a malformed field costs one point rather
        than the whole request.
        """
        lot = ev.get("lot")
        if not lot:
            return

        # A restarted feed republishes onto the same topic with the same lot
        # ids on a fresh timeline, and the topic outlives any one run. Two
        # histories for one lot interleave into fiction -- a lot reading as
        # finished at t=182871 and 21 steps from done at t=221108, which is
        # what this guard was written for.
        #
        # An unstamped point cannot be attributed to a run, so once any run is
        # known, unstamped points are dropped rather than merged. They come
        # from a feed older than this field and are always the stale side.
        run = ev.get("run")
        if run:
            if run != self.burndown_run:
                self.burndown_run = run
                self.burndown.clear()
                self.lot_meta.clear()
                self.sim_t = None
                self.sim_t_at = None
        elif self.burndown_run is not None:
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
                "hot": ev.get("hot") == "1",
            }
        # Route length is constant for the life of the lot. Rework moves already
        # processed steps back onto the front of the route: the lot has more
        # steps *left*, but the total it must pass through is unchanged. It has
        # gone back in the line, not been given extra work.
        meta["route"] = int(f("route")) or meta.get("route", 0)
        meta["left"] = left
        meta["state"] = state
        meta["last_t"] = t
        meta["rem_s"] = f("rem_s")

        self._advance_sim(t)

        self.burndown.append((lot, t, left, ev.get("reason") or "none",
                              f("wq"), f("wb"), f("wp"), f("rem_s")))

        # lot_meta would otherwise outlive the ring and grow without bound over
        # a long run. Trim completed lots first: an active lot's metadata is
        # still needed to draw its line.
        if len(self.lot_meta) > 6000:
            done = [k for k, m in self.lot_meta.items() if m.get("state") == "done"]
            for k in done[:2000]:
                self.lot_meta.pop(k, None)

    def burndown_view(self, cohorts=None, max_lots=400, want_points=False):
        """Group the ring into per-lot series. Takes the lock only to copy."""
        with self.lock:
            points = list(self.burndown)
            meta = {k: dict(v) for k, v in self.lot_meta.items()}
            sim_t = self.sim_t
            warm_t = self.warm_t

        if want_points:
            # The rate model is fitted over the whole ring, not just the
            # requested cohort: one cohort is 4-6 lots, far too few samples to
            # estimate a per-product rate from.
            self._all_points = points

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
        return series, meta, sim_t, warm_t

    def note_timeline(self, run, day, source):
        """Record which run/day a record came from, and whether they agree.

        The mirror has no memory of its own, so nothing stopped it drawing a
        day-5 snapshot against a day-30 decision stream. Both looked healthy
        and the composite was nonsense. Comparing the run id the snapshot
        carried against the one the live stream carries is the cheapest
        possible check, and it is the difference between a wrong dashboard and
        a dashboard that says it is wrong.
        """
        if source == "snapshot":
            if run:
                self.snapshot_run = run
            if day is not None:
                self.snapshot_day = day
        else:
            if run:
                self.stream_run = run
            if day is not None:
                self.stream_day = day

    def timeline(self):
        """Snapshot/stream provenance, and whether they are the same run."""
        with self.lock:
            snap_run, stream_run = self.snapshot_run, self.stream_run
            snap_day, stream_day = self.snapshot_day, self.stream_day
        # Unknown is not the same as disagreeing: before either side has been
        # seen there is nothing to contradict, and claiming a fault then would
        # train people to ignore the badge.
        if snap_run and stream_run:
            ok = snap_run == stream_run
        else:
            ok = None
        return {
            "snapshot_run": snap_run, "snapshot_day": snap_day,
            "stream_run": stream_run, "stream_day": stream_day,
            "consistent": ok,
        }

    def add_decision(self, d):
        self.note_timeline(d.get("run"), _as_float(d.get("day")), "stream")
        with self.lock:
            self.decisions.append({**d, "ts": time.time()})
            # Decisions carry the simulated clock as a day number. Advancing
            # sim_t from them as well as from burndown progress is what keeps
            # the live chart's x axis working under --no-burndown, which turns
            # the only other sim-timestamped topic off.
            day = _as_float(d.get("day"))
            if day is not None:
                self._advance_sim(day * 86400.0)
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

    def _advance_sim(self, t):
        """Move the simulated clock forward. Caller holds the lock.

        Monotonic on purpose: events from several topics arrive interleaved and
        slightly out of order, and a clock that stepped backwards would make
        the live chart draw a sample to the left of the one before it.
        """
        if t is None:
            return
        if self.sim_t is None or t > self.sim_t:
            self.sim_t = t
            # Wall clock at which that reading was taken, so a consumer can
            # tell a stalled feed from a fab that is genuinely idle.
            self.sim_t_at = time.time()

    def snapshot(self):
        # Read outside the lock: it touches the filesystem, and the ingest
        # thread should never wait on a stat() to record an event.
        sim = read_sim_control()
        with self.lock:
            now = time.time()
            sim["t"] = self.sim_t
            sim["t_at"] = self.sim_t_at
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
                "timeline": {
                    "snapshot_run": self.snapshot_run,
                    "snapshot_day": self.snapshot_day,
                    "stream_run": self.stream_run,
                    "stream_day": self.stream_day,
                    "consistent": (None if not (self.snapshot_run and self.stream_run)
                                   else self.snapshot_run == self.stream_run),
                },
                # The simulated clock, and the pacing that clock is running
                # at, on the same frame as the counts they describe. Polled
                # separately they would disagree: a live chart plotted against
                # sim time has to know the clock for *this* sample, not the
                # clock as of whenever a separate poll last landed.
                "sim": sim,
            }


def read_sim_control():
    """Playback pacing as the feed last wrote it.

    `available` is false when no feed has ever written the file: a Kafka feed
    from somewhere else has no speed to report, and neither the badge nor the
    chart should invent one.
    """
    try:
        with open(SIM_CONTROL_FILE) as f:
            ctl = json.load(f)
    except (OSError, ValueError):
        return {"available": False, "speed": None, "paused": False}
    return {
        "available": True,
        "speed": _as_float(ctl.get("speed")),
        "paused": bool(ctl.get("paused")),
        "updated": ctl.get("updated"),
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


def apply_state_record(topic, ev):
    """Apply one compacted state record. Shared by bootstrap and the live loop.

    A snapshot record is authoritative: it replaces whatever the mirror
    believed, in both directions. Extracted so the two paths cannot drift --
    a snapshot arriving after boot has to land exactly as one arriving during
    it, and before this was shared, one path simply did not exist.
    """
    mirror.note_timeline(ev.get("run"), _as_float(ev.get("day")), "snapshot")
    if topic == "fab.lot.state":
        lot = ev.get("lot")
        if not lot:
            return 0
        tool = ev.get("tool")
        with mirror.lock:
            mirror.lots_ready.pop(lot, None)
            mirror.in_flight.pop(lot, None)
            if tool:
                mirror.in_flight[lot] = tool
            else:
                mirror.lots_ready[lot] = ev
            # Same record also carries the warm-up burndown, which is the only
            # place an active lot's past exists.
            mirror._apply_lot_state(ev)
        return 1
    tool = ev.get("tool")
    if not tool:
        return 0
    online = ev.get("online") != "0"
    with mirror.lock:
        mirror.tools[tool] = {"online": online, "last_seen": time.time()}
        # Snapshots restore down tools too. Arm the watchdog for them, or a
        # tool that was offline when the snapshot was taken is never swept.
        if online:
            mirror.down_since.pop(tool, None)
            mirror.recovered_by.pop(tool, None)
        else:
            mirror.down_since.setdefault(tool, time.time())
    return 1


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
            mirror.note_timeline(ev.get("run"), _as_float(ev.get("day")),
                                 "snapshot")
            if msg.topic() == "fab.lot.state":
                lots += apply_state_record(msg.topic(), ev)
            else:
                tools += apply_state_record(msg.topic(), ev)
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
    # The state topics are consumed live as well as at bootstrap. A snapshot
    # published after the API started -- which is the normal case, since the
    # feed is a separate process people start whenever -- would otherwise never
    # be applied: bootstrap already ran against an empty topic and nothing
    # would look again. That produced a dashboard showing 41 lots instead of
    # 2,164. A compacted state topic is current truth whenever it arrives, not
    # only at boot.
    c.subscribe(["fab.lot.events", "fab.tool.events", "fab.dispatch.decisions",
                 "fab.lot.burndown", "fab.lot.state", "fab.tool.state"])
    app.logger.info("kafka mirror attached to %s", KAFKA_BROKERS)

    while True:
        msg = c.poll(1.0)
        if msg is None or msg.error():
            continue
        payload = msg.value().decode("utf-8", "replace")
        topic = msg.topic()
        if topic == "fab.dispatch.decisions":
            mirror.add_decision(parse_envelope(payload))
        elif topic in ("fab.lot.state", "fab.tool.state"):
            apply_state_record(topic, parse_envelope(payload))
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
                  "/api/sim/control",
                  # Planning the live pool. POST because it carries a solve
                  # budget, not because it writes anything: it runs
                  # libfabslate over a SNAPSHOT of the ready pool in this
                  # process and returns the result. No path to the dispatcher,
                  # no fab state touched.
                  "/api/slate/plan"}


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
    """Liveness, plus whether this instance refuses writes."""
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
    """Full mirror snapshot: tools, queues, counters and the sim clock."""
    return jsonify(mirror.snapshot())


# ---------------------------------------------------------------------------
# Routes. The product routes are static reference data extracted from SMT2020
# by viz/extract_routes.py -- a route does not change while the fab runs, so it
# is read once and cached rather than mirrored from the event stream. What is
# live is which cohorts are currently walking each route, and those are grouped
# out of the burndown mirror on demand.
# ---------------------------------------------------------------------------

ROUTES_FILE = os.getenv(
    "ROUTES_FILE",
    os.path.join(os.path.dirname(os.path.abspath(__file__)),
                 "..", "..", "viz", "routes_lvhm.json"))
# part.txt is the dataset's own product -> route table. It is read when present
# so a dataset that does not use the part_N/route_N convention still resolves;
# the derived fallback below only covers the convention.
PART_TABLE = os.getenv(
    "PART_TABLE",
    os.path.join(os.path.dirname(os.path.abspath(__file__)),
                 "..", "..", "data", "smt2020", "SMT2020_LVHM", "part.txt"))

_routes_cache = {"mtime": None, "data": None}

# Lane grouping for the route map, mirroring viz/build_route_diagram.py: a
# wafer moves through these modules in a loop, so ordering lanes this way makes
# the litho/etch cycle read as one band rather than as scattered rows. Areas
# absent from a given route are dropped client-side, not here.
ROUTE_ZONES = [
    ("Thermal / Deposition", ["Diffusion", "Dielectric", "TF", "TF_Met"]),
    ("Patterning", ["Litho", "Litho_Met"]),
    ("Etch", ["Dry_Etch", "Wet_Etch"]),
    ("Doping & Planarization", ["Implant", "Planar"]),
    ("Metrology & Queue", ["Def_Met", "Delay_32", "Delay"]),
]
# Metrology is an area-name convention in SMT2020 rather than a column: the
# *_Met bays are the measurement steps, and every rework point in LVHM sits on
# one of them.
MEASURE_SUFFIX = "_Met"


def _load_part_table():
    """PART -> route key ("part_3" -> "3"), from the dataset's part.txt."""
    try:
        with open(PART_TABLE) as f:
            header = f.readline().rstrip("\n").split("\t")
            idx = {n: i for i, n in enumerate(header)}
            out = {}
            for line in f:
                if not line.strip():
                    continue
                c = line.rstrip("\n").split("\t")
                # ROUTE is "r_3"; the routes JSON keys on the bare "3".
                route = c[idx["ROUTE"]]
                out[c[idx["PART"]]] = route[2:] if route.startswith("r_") else route
            return out
    except Exception:
        return {}


def _load_routes():
    """Parsed routes JSON, reloaded only when the file changes on disk."""
    try:
        mtime = os.path.getmtime(ROUTES_FILE)
    except OSError:
        return None
    if _routes_cache["mtime"] != mtime:
        with open(ROUTES_FILE) as f:
            doc = json.load(f)
        parts = _load_part_table()
        if not parts:
            parts = {"part_%s" % k: k for k in doc.get("routes", {})}
        # Both directions are precomputed: the index needs product -> route and
        # every detail lookup needs route -> product.
        doc["_by_part"] = parts
        doc["_part_of"] = {v: k for k, v in parts.items()}
        _routes_cache.update(mtime=mtime, data=doc)
    return _routes_cache["data"]


def _route_summary(product, key, r):
    """The index row. Deliberately omits `seq` and `visits` -- those run to a
    few hundred entries per route, and inlining them would turn a ten-product
    index into a megabyte of JSON."""
    areas = r.get("areas", {})
    return {
        "product": product,
        "route": r.get("id", "r_%s" % key),
        "key": key,
        "n_steps": r.get("n_steps", 0),
        "n_visits": r.get("n_visits", 0),
        "n_areas": len(areas),
        "areas": areas,
        "batch_steps": r.get("batch_steps", 0),
        "sampled": r.get("sampled", 0),
        # Measurement steps, and how many of those are sampled -- a sampled
        # step measures only a fraction of lots, so the two differ.
        "measure_steps": r.get("measure_steps", sum(
            n for a, n in areas.items() if a.endswith(MEASURE_SUFFIX))),
        "rework_steps": len(r.get("rework", [])),
        # The bay a lot of this product spends the most steps in. It is the one
        # thing that distinguishes the ten LVHM routes at a glance.
        "dominant_area": max(areas, key=areas.get) if areas else None,
    }


@app.get("/api/routes")
def routes_index():
    """Product route index.

    One row per saleable product, with its route's step and visit counts and
    per-area step totals. Summaries only: the full step sequence is served per
    product by /api/routes/<product>.
    """
    doc = _load_routes()
    if not doc:
        return jsonify({"error": "routes file not found: %s" % ROUTES_FILE}), 404
    rows = []
    for product, key in doc["_by_part"].items():
        r = doc["routes"].get(key)
        if r:
            rows.append(_route_summary(product, key, r))
    # part_2 before part_10: the products are numbered, and a lexical sort puts
    # the tenth product second.
    rows.sort(key=lambda x: (_as_int(x["key"], 0) or 0, x["product"]))

    # How many lots and cohorts of each product the mirror is holding, so the
    # index can say which routes are live rather than only which exist.
    _, meta, sim_t, warm_t = mirror.burndown_view(cohorts=[])
    lots = Counter(m["part"] for m in meta.values())
    cohorts = defaultdict(set)
    for m in meta.values():
        cohorts[m["part"]].add(m["cohort"])
    for row in rows:
        row["lots_tracked"] = lots.get(row["product"], 0)
        row["cohorts_tracked"] = len(cohorts.get(row["product"], ()))

    return jsonify({"dataset": doc.get("dataset"), "areas": doc.get("areas", []),
                    "zones": _zones(doc), "now_t": sim_t, "warm_t": warm_t,
                    "products": rows})


def _zones(doc):
    """Lane groups, filtered to the areas this dataset actually has."""
    known = set(doc.get("areas", []))
    out = []
    for name, areas in ROUTE_ZONES:
        present = [a for a in areas if a in known]
        if present:
            out.append({"name": name, "areas": present})
    # An area the grouping does not know about would silently vanish from every
    # lane map, so it is surfaced rather than dropped.
    placed = {a for z in out for a in z["areas"]}
    extra = sorted(known - placed)
    if extra:
        out.append({"name": "Other", "areas": extra})
    return out


@app.get("/api/routes/<path:product>")
def route_detail(product):
    """One product's route, plus a sample of the cohorts currently walking it.

    `visits` collapses consecutive same-area steps into one visit to that bay,
    which is what makes a 500-step route readable: the re-entrancy that defines
    a wafer fab shows up as repeated visits to the same area rather than as a
    flat list. `cohorts` is a live sample from the burndown mirror, ranked by
    last movement, and is empty when no feed is attached.
    """
    doc = _load_routes()
    if not doc:
        return jsonify({"error": "routes file not found: %s" % ROUTES_FILE}), 404
    key = doc["_by_part"].get(product)
    # The route id resolves as well as the product name, so a link built from
    # "r_3" or "3" lands on the same page as "part_3" instead of 404ing.
    if key is None:
        cand = product[2:] if product.startswith("r_") else product
        if cand in doc["routes"]:
            key, product = cand, doc["_part_of"].get(cand, product)
    r = doc["routes"].get(key) if key is not None else None
    if not r:
        return jsonify({"error": "unknown product: %s" % product}), 404

    _, meta, sim_t, warm_t = mirror.burndown_view(cohorts=[])
    rows = [c for c in _cohort_rows(meta) if c["part"] == product]
    limit = _as_int(request.args.get("limit"), 8) or 8

    detail = _route_summary(product, key, r)
    detail.update({
        "dataset": doc.get("dataset"),
        "areas_order": doc.get("areas", []),
        "zones": _zones(doc),
        "visits": r.get("visits", []),
        "transitions": r.get("transitions", []),
        "rework": r.get("rework", []),
        "area_visits": r.get("area_visits", {}),
        "now_t": sim_t,
        "warm_t": warm_t,
        "cohorts": rows[:limit],
        "total_cohorts": len(rows),
    })
    return jsonify(detail)


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
        down_since = mirror.down_since.get(tool_id)
        recovered = mirror.recovered_by.get(tool_id)
    return {
        "id": tool_id,
        "group": tool_group(tool_id),
        "online": meta.get("online", True),
        "last_seen": meta.get("last_seen"),
        "down_reason": meta.get("reason"),
        "down_s": meta.get("down_s"),
        "down_since": down_since,
        # Set only when the mirror inferred the recovery rather than being
        # told: "activity" (the tool started a lot) or "watchdog" (down past
        # the TTL with no word). Absent on a normal online status event.
        "recovered_by": recovered,
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


@app.get("/api/tools/availability")
def tools_availability():
    """Online-tool count over time, for the strip at the top of the index.

    Returned as parallel arrays rather than objects: at 2,880 points the object
    form is several times the bytes for the same numbers, and this polls every
    few seconds. `total` is the roster size -- the reference line the series
    should be sitting on.
    """
    with mirror.lock:
        pts = list(mirror.availability)
        stuck = len(mirror.down_since)
        inferred = Counter(mirror.recovered_by.values())
        ids = set(mirror.tools) | set(mirror.tool_stats)
        total = len(ids)
        online = sum(1 for t in ids
                     if mirror.tools.get(t, {}).get("online", True))
    return jsonify({
        "ts":      [round(p[0], 1) for p in pts],
        "online":  [p[1] for p in pts],
        # Historical, not just current: the roster grows as tools announce
        # themselves, so a flat line drawn at today's total would misread the
        # early part of the run as an outage.
        "total":   [p[2] for p in pts],
        "now":     {"online": online, "total": total, "down": total - online},
        "down_now": stuck,
        "recovered": dict(inferred),
        "ttl_s":   TOOL_DOWN_TTL_S,
        "sample_s": AVAIL_SAMPLE_S,
    })


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
    """One tool: its row, plus its recent decisions and events."""
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
    """Tail of the raw event feed, oldest first."""
    n = min(int(request.args.get("limit", 100)), 500)
    with mirror.lock:
        return jsonify(list(mirror.events)[-n:])


@app.get("/api/decisions")
def decisions():
    """Tail of the dispatch decision feed, oldest first."""
    n = min(int(request.args.get("limit", 100)), 500)
    with mirror.lock:
        return jsonify(list(mirror.decisions)[-n:])


# Rate model. Days per step, learned from what this fab has actually done
# rather than from the route's nominal process times: the nominal numbers omit
# queueing, which is most of the cycle, and would project every lot finishing
# far too early.
#
# Keyed by (product, lot type) with an explicit fallback chain, because those
# are the two parameters that visibly change the rate here. Hot lots hold
# priority 20 against 10 and jump queues, so a rate learned from regular lots
# does not describe them; and LVHM's ten products have routes from 242 to 583
# steps through different tool families, so per-product is not the same as
# fab-wide. `basis` is reported with every projection so a thin cell is
# visible rather than quietly averaged away.
RATE_MIN_SAMPLES = int(os.getenv("RATE_MIN_SAMPLES", "8"))


def _median(xs):
    xs = sorted(xs)
    n = len(xs)
    if not n:
        return None
    return xs[n // 2] if n % 2 else 0.5 * (xs[n // 2 - 1] + xs[n // 2])


def rate_model(points, meta):
    """Seconds per step, bucketed by (part, hot), (part), (hot) and overall.

    A sample is one observed forward move: the wall of simulated time between
    two consecutive points, divided by the steps actually completed between
    them. Backward moves (rework) are skipped -- they are not progress, and
    dividing by a negative step count would make the rate meaningless. Their
    cost still lands in the model, because the extra steps a reworked lot
    subsequently redoes are themselves sampled.
    """
    buckets = {}
    prev = {}
    for lot, t, left, *_ in points:
        p = prev.get(lot)
        prev[lot] = (t, left)
        if p is None:
            continue
        dt, dsteps = t - p[0], p[1] - left
        if dsteps <= 0 or dt <= 0:
            continue
        m = meta.get(lot)
        if m is None:
            continue
        per_step = dt / dsteps
        hot = bool(m.get("hot"))
        for key in ((m["part"], hot), (m["part"], None), (None, hot), (None, None)):
            buckets.setdefault(key, []).append(per_step)

    return {k: {"rate_s": _median(v), "n": len(v)} for k, v in buckets.items()}


def project(meta_row, rates, now):
    """Naive completion estimate for one lot.

    Naive is the point: it assumes the lot keeps moving at the rate its own
    product and lot type have shown, with no further rework, no tool
    downtime, and no change in queueing. It is a straight-line reference to
    read the real line against, not a forecast.
    """
    left = meta_row.get("left")
    if left is None or meta_row.get("state") in ("done", "scrapped"):
        return None

    part, hot = meta_row["part"], bool(meta_row.get("hot"))
    for key, basis in (((part, hot), "part+type"), ((part, None), "part"),
                       ((None, hot), "type"), ((None, None), "fab")):
        r = rates.get(key)
        if r and r["rate_s"] and r["n"] >= (RATE_MIN_SAMPLES if basis != "fab" else 1):
            # Project from now, not from the last event: a lot that has been
            # sitting for two days is still sitting, and starting the ray at
            # its last move would quietly forgive that wait.
            start = max(meta_row.get("last_t", 0.0), now or 0.0)
            eta = start + left * r["rate_s"]
            due = meta_row.get("due")
            return {
                "rate_s": round(r["rate_s"], 1),
                "basis": basis,
                "n": r["n"],
                "start_t": start,
                "eta_t": eta,
                "due_t": due,
                # Positive = projected to finish before its due date.
                "slack_s": (due - eta) if due else None,
            }
    return None


def lot_stats(m, pts, now):
    """Summary for one lot, computed here so the chart and the table cannot
    disagree about the same number."""
    route = m.get("route") or 0
    left = m.get("left") or 0
    done = max(route - left, 0)

    # Rework shows as the burndown going back up: steps already completed are
    # put back in front of the lot. Count the jogs and the steps re-queued --
    # the route length itself never changes.
    rework_events = 0
    rework_steps = 0
    for a, b in zip(pts, pts[1:]):
        if b["left"] > a["left"]:
            rework_events += 1
            rework_steps += b["left"] - a["left"]

    wq = sum(p.get("wq", 0) for p in pts)
    wb = sum(p.get("wb", 0) for p in pts)
    wp = sum(p.get("wp", 0) for p in pts)

    last_t = m.get("last_t", 0.0)
    return {
        "route": route,
        "steps_done": done,
        "steps_left": left,
        "pct_complete": round(100.0 * done / route, 1) if route else None,
        "rework_events": rework_events,
        "rework_steps": rework_steps,
        "queue_s": round(wq, 1),
        "batch_wait_s": round(wb, 1),
        "process_s": round(wp, 1),
        # How long since this lot last moved. A large value with state=active
        # is the whole point of extending the flat line to now.
        "idle_s": round(max((now or 0.0) - last_t, 0.0), 1),
        "elapsed_s": round(max((now or 0.0) - m.get("release", 0.0), 0.0), 1),
    }


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
    _, meta, sim_t, warm_t = mirror.burndown_view(cohorts=[])
    rows = _cohort_rows(meta)
    return jsonify({
        "now_t": sim_t,
        "warm_t": warm_t,
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
    series, meta, sim_t, warm_t = mirror.burndown_view(cohorts=[cohort],
                                                       want_points=True)
    # A lot can have warm-up history and no live points yet -- it has not moved
    # since the snapshot. Dropping those would hide exactly the stalled lots
    # worth looking at, so they are drawn from history alone.
    for lot, m in meta.items():
        if m.get("cohort") == cohort and m.get("history") and lot not in series:
            series[lot] = []

    if not series:
        return jsonify({"cohort": cohort, "now_t": sim_t, "warm_t": warm_t,
                        "lots": [], "note": "no points held for this cohort"}), 200

    rates = rate_model(getattr(mirror, "_all_points", []), meta)

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
            "hot": bool(m.get("hot")),
            "state": m.get("state", "active"),
            # Warm-up history and live points are kept apart rather than
            # concatenated: the chart draws them differently, and merging them
            # here would throw away the distinction the user asked to see.
            "history": m.get("history", []),
            "points": pts,
            "projection": project(m, rates, sim_t),
            "stats": lot_stats(m, pts, sim_t),
        })
    lots.sort(key=lambda r: r["lot"])
    return jsonify({"cohort": cohort, "now_t": sim_t, "warm_t": warm_t,
                    "lots": lots,
                    "rate_basis_counts": {
                        f"{k[0] or 'fab'}/{'hot' if k[1] else ('reg' if k[1] is False else 'any')}": v["n"]
                        for k, v in sorted(rates.items(), key=lambda kv: -kv[1]["n"])[:8]
                    }})


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

    The same reading the state frames carry (see read_sim_control), plus the
    menu of speeds, which only the dashboard's control needs.
    """
    ctl = read_sim_control()
    if not ctl["available"]:
        return jsonify({"available": False, "speeds": SIM_SPEEDS})
    return jsonify({**ctl, "speeds": SIM_SPEEDS})


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
    """Whether the assistant is configured, and which model backs it."""
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


def availability_loop():
    """Watchdog + sampler.

    Cheap enough to run unconditionally and independent of the transport: one
    pass over the roster every AVAIL_SAMPLE_S, which even for the full LVHM
    fab is a few thousand dict lookups. It runs whether the feed is Kafka, a
    file, or nothing at all -- a stalled feed is exactly when a tool is most
    likely to be stranded offline.
    """
    while True:
        try:
            mirror.sweep_and_sample()
        except Exception as e:
            print(f"[availability] {e!r}", file=sys.stderr, flush=True)
        time.sleep(AVAIL_SAMPLE_S)


def start_feeds():
    """FEED_FILE replaces Kafka rather than supplementing it: running both
    would interleave two sources into one mirror and make the state
    unattributable."""
    threading.Thread(target=availability_loop, daemon=True).start()
    if FEED_FILE:
        threading.Thread(target=feed_file_loop, daemon=True).start()
    else:
        threading.Thread(target=kafka_consumer_loop, daemon=True).start()


# ---------------------------------------------------------------------------
# Slate — the CP-SAT planner behind bench/tools/slate_rule.py, exposed live.
#
# Two things the dashboard needs and could not get before:
#   * plan the CURRENT ready pool through the same libfabslate.so the benchmark
#     uses, so what is shown is what the rule would do -- not a reimplementation
#   * read the head-to-head comparison runs, so slate can be seen next to
#     fifo/cr on identical demand
#
# The library is loaded lazily and its absence is reported, never faked: a
# dashboard that invents a plan when the planner is missing is worse than one
# that says it is missing. See docs/adr/0009.
# ---------------------------------------------------------------------------

SLATE_LIB = os.getenv("FABSLATE_LIB",
                      os.path.join(os.path.dirname(os.path.dirname(
                          os.path.abspath(__file__))), "libfabslate.so"))
BENCH_RESULTS = os.getenv("BENCH_RESULTS", os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "bench", "results"))

_slate = {"planner": None, "error": None, "tools_sig": None}
_slate_lock = threading.Lock()


def _slate_planner():
    """Lazily build a planner over the live tool set. Returns (planner, error)."""
    sys.path.insert(0, os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
        "bench", "tools"))
    try:
        import fabslate
    except Exception as e:                      # pragma: no cover
        return None, f"fabslate module not importable: {e}"

    with mirror.lock:
        tools = [{"tool_id": t, "family": tool_group(t) or "UNKNOWN",
                  "capacity": 1, "online": True, "speed": 1.0}
                 for t in sorted(mirror.tools)]
    if not tools:
        return None, "no tools known yet -- start a feed"

    sig = tuple(t["tool_id"] for t in tools)
    with _slate_lock:
        if _slate["planner"] is not None and _slate["tools_sig"] == sig:
            return _slate["planner"], None
        try:
            p = fabslate.Planner("cpsat", lib_path=SLATE_LIB)
            p.set_tools(tools)
        except Exception as e:
            _slate["error"] = str(e)
            return None, str(e)
        _slate["planner"] = p
        _slate["tools_sig"] = sig
        return p, None


@app.get("/api/slate/status")
def slate_status():
    """Is the planner usable, and with which solver."""
    p, err = _slate_planner()
    if p is None:
        return jsonify({"available": False, "lib": SLATE_LIB, "error": err})
    return jsonify({
        "available": True,
        "lib": SLATE_LIB,
        "solver": p.solver,
        # False means OR-Tools is not linked and every "cpsat" number is really
        # greedy. The UI must show this, not bury it.
        "solver_linked": p.solver_available,
        "tools": len(_slate["tools_sig"] or ()),
    })


@app.post("/api/slate/plan")
def slate_plan():
    """Plan the live ready pool and return the slate.

    Needs the route fields sim_feed emits on LOT_READY (fam/setup/part/...).
    A pool without them cannot be decomposed by station family, and that is
    reported rather than papered over with a guess.
    """
    body = request.get_json(silent=True) or {}
    budget = float(body.get("budget_s", 0.25))

    p, err = _slate_planner()
    if p is None:
        return jsonify({"error": err or "planner unavailable"}), 503

    with mirror.lock:
        raw = list(mirror.lots_ready.items())
    lots, missing = [], 0
    for lid, ev in raw:
        fam = ev.get("fam")
        if not fam:
            missing += 1
            continue
        lots.append({
            "lot_id": lid, "family": fam,
            "setup_group": ev.get("setup", "") or "",
            "step": ev.get("recipe", ""), "part": ev.get("part", ""),
            "batch_min": int(ev.get("bmin", 1) or 1),
            "batch_max": int(ev.get("bmax", 1) or 1),
            "wafers": int(ev.get("wafers", 25) or 25),
            "priority": float(ev.get("prio", 1.0) or 1.0),
            "step_process_s": float(ev.get("proc", 0.0) or 0.0),
            "due_s": float(ev.get("due", -1.0) or -1.0),
        })
    if not lots:
        return jsonify({
            "error": "no plannable lots in the live pool",
            "ready_seen": len(raw), "missing_route_fields": missing,
            "hint": "sim_feed must be running and emitting fam/setup/part on "
                    "LOT_READY",
        }), 400

    tokens, stats = p.plan(lots, budget_s=budget)
    by_family = {}
    for l in lots:
        by_family[l["family"]] = by_family.get(l["family"], 0) + 1
    return jsonify({
        "stats": stats,
        "lots_planned": len(lots),
        "missing_route_fields": missing,
        "families": sorted(({"family": f, "waiting": n}
                            for f, n in by_family.items()),
                           key=lambda r: -r["waiting"])[:25],
        "assignments": [
            {"lot_id": lots[i]["lot_id"], "family": lots[i]["family"],
             "tool": tid, "alternate": alt, "rank": rank,
             "expected_process_s": round(exp, 1)}
            for i, tid, alt, rank, exp in tokens
        ],
    })


@app.get("/api/slate/compare")
def slate_compare():
    """Head-to-head runs written by bench/tools/compare.py.

    ?run=<name> returns one; no argument lists what is available. This is the
    fifo/cr/slate table from docs/adr/0002, served to the dashboard rather than
    left in a terminal.
    """
    want = request.args.get("run")
    try:
        names = sorted(f for f in os.listdir(BENCH_RESULTS)
                       if f.startswith("compare_") and f.endswith(".json"))
    except OSError:
        names = []
    if not want:
        runs = []
        for n in names:
            try:
                with open(os.path.join(BENCH_RESULTS, n)) as f:
                    d = json.load(f)
                runs.append({"name": n, "dataset": d.get("dataset"),
                             "days": d.get("days"), "seed": d.get("seed"),
                             "solver": d.get("solver"),
                             "rules": [r["rule"] for r in d.get("rows", [])]})
            except Exception:
                continue
        return jsonify({"dir": BENCH_RESULTS, "runs": runs})

    if want not in names:
        return jsonify({"error": f"no such run: {want}"}), 404
    with open(os.path.join(BENCH_RESULTS, want)) as f:
        return jsonify(json.load(f))


# Swagger UI at /docs, ReDoc at /redoc, spec at /openapi.json. Generated from
# the URL map, so new routes document themselves; see openapi.ENRICH for the
# body/response schemas a URL map cannot infer.
register_docs(app)


if __name__ == "__main__":
    start_feeds()
    # PORT so a test run can stand beside a dev API instead of fighting it for
    # :8000. The container path pins 8000 and does not set this.
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "8000")), threaded=True)
else:
    start_feeds()
