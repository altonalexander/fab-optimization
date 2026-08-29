# api/assistant.py — Claude on Google Vertex AI, grounded in live fab state.
#
# ZONE 3 boundary component. Same read-only rule as the rest of the API: the
# assistant can READ live state and RUN SCENARIOS against a cloned registry.
# It has no path to the dispatcher and cannot change the fab.
#
# Grounding strategy: the model is never asked to recall fab numbers. Every
# figure it states comes from a tool result injected into the conversation. A
# dispatch assistant that hallucinates a tool ID is worse than no assistant.

import json
import os
import subprocess

# Vertex AI hosts Claude models; the Anthropic SDK ships a Vertex client.
#   pip install "anthropic[vertex]"
#   gcloud auth application-default login   (or a service account on the pod)
try:
    from anthropic import AnthropicVertex
    _SDK = True
except ImportError:
    AnthropicVertex = None
    _SDK = False

MODEL       = os.getenv("VERTEX_MODEL", "claude-sonnet-4-5@20250929")
REGION      = os.getenv("VERTEX_REGION", "us-east5")
PROJECT     = os.getenv("GOOGLE_CLOUD_PROJECT", "")
MAX_TOKENS  = int(os.getenv("ASSISTANT_MAX_TOKENS", "1500"))

SYSTEM = """You are the dispatch assistant for a 300mm semiconductor fab.
You help a fab engineer interpret live dispatch state and what-if scenarios.

GROUNDING RULES — these are absolute:
- Every number, tool ID, lot ID, and recipe you state must come from a tool
  result in this conversation. Never recall or invent one.
- If you don't have the data, call a tool. If a tool can't get it, say so
  plainly rather than estimating.
- When you don't know why something happened, say you don't know and name what
  data would answer it.

DOMAIN CONTEXT you may reason from:
- Tool kinds: SINGLE_WAFER (one lot, recipe-change setup), BATCH_FURNACE
  (fixed process time, needs a minimum batch to fire), CLUSTER (per-chamber
  qualification, parallel), LITHO_SCANNER (reticle is exclusive — one reticle
  cannot be on two scanners), METROLOGY (sampled; skipping is valid),
  PROBE_TESTER (probe card must match product; hot/cold soak is expensive).
- Common reasons a lot is unassigned: no qualified tool, no free capacity,
  batch below minimum, reticle held elsewhere, no matching probe card.
- Three horizons: strategic (weekly MILP), tactical (10-30s CP-SAT), and the
  operational fast path (sub-millisecond, no solving — it reads a precomputed
  slate).

STYLE: concise and direct. Lead with the answer. An engineer is reading this
mid-shift. Prefer a short list over a paragraph. No preamble.

You are READ-ONLY. You can inspect state and simulate scenarios against a
cloned registry. You cannot change the running fab, and you should say so if
asked to."""

TOOLS = [
    {
        "name": "get_fab_state",
        "description": "Current live fab state: ready/in-flight lot counts, "
                       "completed total, throughput, and per-tool online status. "
                       "Call this before answering anything about right now.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "get_recent_events",
        "description": "Recent lot and tool events from the Kafka mirror. Use "
                       "to explain what just happened or spot a pattern.",
        "input_schema": {
            "type": "object",
            "properties": {"limit": {"type": "integer",
                                     "description": "how many, max 100"}},
        },
    },
    {
        "name": "run_scenario",
        "description": "Run a what-if against a CLONED registry using the same "
                       "C++ planner the dispatcher uses. Takes tools down and "
                       "re-plans, returning a baseline-vs-scenario diff: which "
                       "lots reroute, which become unassignable. Use this for "
                       "any 'what if X goes down' question.",
        "input_schema": {
            "type": "object",
            "properties": {
                "tools_down": {
                    "type": "array", "items": {"type": "string"},
                    "description": "tool IDs to take offline, e.g. ['LITHO_03']",
                },
            },
            "required": ["tools_down"],
        },
    },
    {
        "name": "explain_unassigned",
        "description": "For the current ready pool, list lots the planner could "
                       "not assign, with the reason for each.",
        "input_schema": {"type": "object", "properties": {}},
    },
]


class FabAssistant:
    """Wraps Claude-on-Vertex with tools bound to the live mirror."""

    def __init__(self, mirror, scenario_runner):
        self.mirror = mirror
        self.run_scenario = scenario_runner
        self.client = None
        self.error = None
        if not _SDK:
            self.error = "anthropic[vertex] not installed"
        elif not PROJECT:
            self.error = "GOOGLE_CLOUD_PROJECT not set"
        else:
            try:
                self.client = AnthropicVertex(region=REGION, project_id=PROJECT)
            except Exception as e:                      # noqa: BLE001
                self.error = f"vertex client init failed: {e}"

    @property
    def available(self):
        return self.client is not None

    # ---- tool implementations --------------------------------------------

    def _exec_tool(self, name, args):
        if name == "get_fab_state":
            snap = self.mirror.snapshot()
            offline = [t for t, v in snap["tools"].items() if not v["online"]]
            return {**snap, "tools_offline": offline}

        if name == "get_recent_events":
            n = min(int(args.get("limit", 40)), 100)
            with self.mirror.lock:
                return {"events": list(self.mirror.events)[-n:]}

        if name == "run_scenario":
            downed = args.get("tools_down", [])
            return self.run_scenario(
                [{"tool_id": t, "online": False} for t in downed])

        if name == "explain_unassigned":
            res = self.run_scenario([])
            if "error" in res:
                return res
            return {"unassigned": res.get("scenario", res).get("unassigned", []),
                    "assigned": res.get("scenario", res).get("assigned", 0)}

        return {"error": f"unknown tool {name}"}

    # ---- conversation -----------------------------------------------------

    def ask(self, messages):
        """
        messages: [{"role":"user"|"assistant","content":str}, ...]
        Returns {"reply": str, "tools_used": [...], "error": str|None}
        Runs the tool-use loop to completion, then returns the final text.
        """
        if not self.available:
            return {"reply": None, "tools_used": [],
                    "error": self.error or "assistant unavailable"}

        convo = [{"role": m["role"], "content": m["content"]} for m in messages]
        used = []

        # Bounded loop: a runaway tool cycle must not hang the request.
        for _ in range(6):
            try:
                resp = self.client.messages.create(
                    model=MODEL, max_tokens=MAX_TOKENS,
                    system=SYSTEM, tools=TOOLS, messages=convo,
                )
            except Exception as e:                       # noqa: BLE001
                return {"reply": None, "tools_used": used, "error": str(e)}

            if resp.stop_reason != "tool_use":
                text = "".join(b.text for b in resp.content if b.type == "text")
                return {"reply": text, "tools_used": used, "error": None}

            convo.append({"role": "assistant", "content": resp.content})
            results = []
            for block in resp.content:
                if block.type != "tool_use":
                    continue
                out = self._exec_tool(block.name, block.input or {})
                used.append({"tool": block.name, "input": block.input})
                results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": json.dumps(out)[:20000],   # cap context growth
                })
            convo.append({"role": "user", "content": results})

        return {"reply": None, "tools_used": used,
                "error": "tool loop exceeded 6 rounds"}
