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
import queue
import subprocess
import threading
import time
from collections import deque, Counter
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

# ---------------------------------------------------------------------------
# Live state, rebuilt from the Kafka event stream.
# Bounded everywhere: an unbounded buffer in a long-running mirror is a leak.
# ---------------------------------------------------------------------------

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
            elif t == "LOT_COMPLETE":
                self.in_flight.pop(ev.get("lot"), None)
                self.throughput.append((time.time(), self.counts["LOT_COMPLETE"]))
            elif t == "TOOL_STATUS":
                self.tools[ev.get("tool")] = {
                    "online": ev.get("online") != "0",
                    "last_seen": time.time(),
                }
        self._fanout({"kind": "event", "topic": topic, "event": ev})

    def add_decision(self, d):
        with self.lock:
            self.decisions.append({**d, "ts": time.time()})
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


# ---------------------------------------------------------------------------
# Read-only guard. Belt and braces with the nginx limit_except.
# ---------------------------------------------------------------------------

SCENARIO_PATHS = {"/api/scenario", "/api/scenario/compare", "/api/chat"}


@app.before_request
def enforce_read_only():
    if not READ_ONLY:
        return None
    if request.method in ("GET", "HEAD", "OPTIONS"):
        return None
    # Scenario POST is permitted: it runs against a cloned registry and has no
    # path back to the dispatcher. Everything else is refused.
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


@app.post("/api/scenario")
def scenario():
    """
    What-if. Shells out to the C++ planner against a CLONED registry.

    We do NOT reimplement dispatch logic here. A scenario answer that diverges
    from what the dispatcher would actually do is worse than no answer.
    """
    body = request.get_json(silent=True) or {}

    if not body.get("lots"):
        # Default to the live ready pool so "what if X goes down" works with
        # no setup — that is the question people actually want to ask.
        with mirror.lock:
            body["lots"] = [
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
    if not body.get("lots"):
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


if __name__ == "__main__":
    threading.Thread(target=kafka_consumer_loop, daemon=True).start()
    app.run(host="0.0.0.0", port=8000, threaded=True)
else:
    threading.Thread(target=kafka_consumer_loop, daemon=True).start()
