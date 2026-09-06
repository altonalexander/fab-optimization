# api/assistant.py — Gemini agents (Google ADK) on Vertex AI, grounded in live
# fab state.
#
# ZONE 3 boundary component. Same read-only rule as the rest of the API: the
# assistant can READ live state and RUN SCENARIOS against a cloned registry.
# It has no path to the dispatcher and cannot change the fab.
#
# Grounding strategy: the model is never asked to recall fab numbers. Every
# figure it states comes from a tool result injected into the conversation. A
# dispatch assistant that hallucinates a tool ID is worse than no assistant.
#
# Shape: one root "dispatch" agent that answers the engineer, routing to two
# specialists it calls as tools -- `state` (what is happening now) and
# `scenario` (what-if against the cloned registry). Each specialist owns the
# tools for its question so the root never mixes a live number with a
# simulated one. Adding a specialist (say, a KPI-history agent over Postgres)
# is one more LlmAgent in AGENTS below; the routing is the root's job.
#
# Runs in-process: the ADK Runner is driven synchronously per request with an
# in-memory session rebuilt from the transcript the UI sends. Nothing persists
# server-side, which keeps the API stateless like every other endpoint.

import asyncio
import json
import os
import threading
import uuid

# Credentials come from Application Default Credentials:
#   pip install google-adk
#   gcloud auth application-default login   (or a service account on the pod)
# The project is GOOGLE_CLOUD_PROJECT when set, otherwise the ADC quota project
# (what `gcloud config set project` stamped at login), so a dev box needs no
# env at all once it has logged in.
try:
    from google.adk.agents import LlmAgent
    from google.adk.events import Event
    from google.adk.runners import Runner
    from google.adk.sessions import InMemorySessionService
    from google.adk.tools import AgentTool
    from google.genai import types as gtypes
    _SDK = True
except ImportError:
    _SDK = False

MODEL       = os.getenv("VERTEX_MODEL", "gemini-2.5-flash")
REGION      = os.getenv("VERTEX_REGION", "us-central1")
PROJECT     = os.getenv("GOOGLE_CLOUD_PROJECT", "")
MAX_TOKENS  = int(os.getenv("ASSISTANT_MAX_TOKENS", "1500"))
APP_NAME    = "fab-dispatch"


def _adc_project():
    """Project recorded with the local ADC, or '' when there is none."""
    try:
        import google.auth
        _, proj = google.auth.default()
        return proj or ""
    except Exception:                                   # noqa: BLE001
        return ""


GROUNDING = """GROUNDING RULES — these are absolute:
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

You are READ-ONLY. You can inspect state and simulate scenarios against a
cloned registry. You cannot change the running fab, and you should say so if
asked to."""

# What the dashboard is and what each page shows, so "what can I do here" and
# "what is this telling me" are answered from this text with no tool call.
# Keep it in step with the UI: the page blurbs below are lifted from the
# headers and info tips the engineer is reading.
APP_GUIDE = """THE APP: "Fab Dispatch" is a read-only dashboard over a simulated
300mm wafer fab (the public SMT2020 testbed replayed by a simulator, ~56 lot
starts/day). Every page reads the same live mirror of the fab's Kafka feed.
Nothing on any page can change the fab. Simulated time runs faster than wall
time; the header pill shows the sim clock and playback speed.

HEADER (every page): KPI tiles for the whole fab.
- WIP: lots released and not yet complete, waiting plus on a tool.
- throughput: lots that completed their whole route in the trailing sim day.
- starts: lots released in the trailing day (the dataset's fixed order
  schedule; starts above throughput means WIP is building).
- cycle time: mean release-to-complete days of lots completed in the trailing
  day.
- on-time delivery: share of lots completed in the trailing day that met
  their due date; hover shows mean tardiness of the late ones.
- tool utilization: share of real tools with a lot processing at the sample
  instant (down tools count as not busy; Delay_* pseudo-tools excluded).
- optimized decisions: share of dispatch decisions in the trailing day made
  by the optimizing dispatcher rather than the default fifo rule. Reads 0%
  while the baseline runs.
- tools down: tools currently offline from a breakdown or PM; click for the
  tool index.

PAGES (tabs):
- live: the WIP chart since day 0, the KPI charts since day 0 (the header
  tiles over time), and the event feed of lot/tool events and dispatch
  decisions as they arrive.
- lots: cohort burndown. A cohort is one product's releases within one day
  (id like part_3-d-11), the lots that can actually batch together. The chart
  shows remaining route steps per lot against sim time; band thickness is
  cohort spread and a widening band means the cohort is desynchronising.
  Above it, each lot's journey strip: last two steps, current, next two. Pick
  a cohort from the control; click a lot to drill in.
- tools: the tool index, busiest first, filterable by type and search. An
  availability strip on top shows whether the roster is intact (the dashed
  line is roster size; the gap beneath it is the outage). A high queue with
  the tool online is where lots are waiting. Open a tool for its decisions,
  queue, setups, and the lots it touched, each linking to its cohort.
- floor: the cleanroom floor map by bay, with an optional heat overlay; click
  a bay to select it, click a tool to open it. Queue-time delays (Delay_*)
  are listed separately because they have no physical position.
- routes: product routes, one per product; open one to see its steps and
  where lots spend queue time, with links to each cohort.
- slate: the precomputed dispatch slate the operational fast path reads:
  which solver built it, how many waiting lots it assigned and how fast, and
  the assignment list. It is built offline with dispatch/build-slate.sh.
- results: every simulation run recorded in the Postgres run store, with
  post-warm-up KPI means and deltas against a chosen baseline, and the KPI
  charts of the selected runs laid over each other, including the run
  streaming right now. Runs differ by dispatching rule (fifo, cr, slate ...).
- topology: the network segmentation of the deployment (zones 0-3, this
  dashboard is zone 3 and read-only), measured link metrics for this
  browser's stream (event rate, lag, heartbeat), and the boundary invariants.

THE ASSISTANT RAIL (you): opens from the header's Assistant button, stays open
across tabs, and on desktop shows a character with suggested questions for
the current page. The trace chips above a reply name the tools it ran."""

ROOT_INSTRUCTION = """You are the dispatch assistant for a 300mm semiconductor
fab. You help a fab engineer use this dashboard and interpret live dispatch
state and what-if scenarios.

The engineer is currently looking at: {view}

Three kinds of question:
1. About the app ("what can I do on this page", "what is this chart telling
   me", "where do I find X"): answer directly from the APP GUIDE below, for
   the page they are on unless they ask about another. No tool call needed.
2. About the fab right now: use the `state` specialist.
3. What-ifs and why-unassigned: use the `scenario` specialist.
A question can mix these ("what is this page telling me right now?" means
explain the page and then read the live state for it).

You have two specialists, exposed as tools:
- `state`: anything about right now — WIP, which tools are up or down, the
  bottleneck, what just happened.
- `scenario`: any "what if" — a tool going down, re-planning, which lots would
  reroute or become unassignable — and any "why are lots unassigned / held /
  waiting" question, because only the planner can say why it could not place
  a lot.
Route each question to the specialist that owns it; a question can need both
(e.g. "is LITHO_03 the bottleneck and what if it goes down?"). Pass the
engineer's question through verbatim plus any tool IDs already mentioned in
the conversation. Answer from what the specialists return; do not add figures
of your own.

STYLE: concise and direct. Lead with the answer. An engineer is reading this
mid-shift. Prefer a short list over a paragraph. No preamble.

""" + GROUNDING + "\n\nAPP GUIDE:\n" + APP_GUIDE

STATE_INSTRUCTION = """You report the fab's live state. Always call
get_fab_state first; call get_recent_events when asked what happened or why.
Reply with the facts the caller needs, tool IDs and numbers exactly as the
tools returned them, in a few short lines.

""" + GROUNDING

SCENARIO_INSTRUCTION = """You run what-if scenarios against a CLONED registry
using the same C++ planner the dispatcher uses. For "what if X goes down" call
run_scenario with those tool IDs. For "why are lots unassigned" call
explain_unassigned. Report the baseline-vs-scenario diff plainly: which lots
reroute, which become unassignable, and the reasons the planner gave.

""" + GROUNDING


def _cap(obj, limit=20000):
    """Bound what a tool hands back to the model so one long event list
    cannot blow the context."""
    s = json.dumps(obj)
    return obj if len(s) <= limit else {"truncated": True, "data": s[:limit]}


class FabAssistant:
    """Root dispatch agent plus specialists, bound to the live mirror."""

    def __init__(self, mirror, scenario_runner):
        self.mirror = mirror
        self.run_scenario = scenario_runner
        self.runner = None
        self.error = None
        self._trace, self._trace_lock = [], threading.Lock()
        self.project = PROJECT or _adc_project()
        if not _SDK:
            self.error = "google-adk not installed"
        elif not self.project:
            self.error = ("no Google Cloud project: set GOOGLE_CLOUD_PROJECT or run "
                          "`gcloud auth application-default login` with a project set")
        else:
            # ADK reads the Vertex routing from the environment.
            os.environ.setdefault("GOOGLE_GENAI_USE_VERTEXAI", "TRUE")
            os.environ.setdefault("GOOGLE_CLOUD_PROJECT", self.project)
            os.environ.setdefault("GOOGLE_CLOUD_LOCATION", REGION)
            try:
                self.root = self._build_agents()
                self.sessions = InMemorySessionService()
                self.runner = Runner(app_name=APP_NAME, agent=self.root,
                                     session_service=self.sessions)
            except Exception as e:                      # noqa: BLE001
                self.error = f"agent init failed: {e}"

    @property
    def available(self):
        return self.runner is not None

    # ---- tools ---------------------------------------------------------------
    # Plain functions: ADK derives each declaration from the signature and
    # docstring, which is what the model sees. Keep the docstrings honest.

    def _tools(self):
        mirror, planner = self.mirror, self.run_scenario
        # The specialists run inside AgentTool's nested runner, whose events do
        # not surface on the outer stream, so each data tool records itself
        # here and ask() reads the list back. One trace per assistant: two
        # chats hitting the API at the same instant could interleave, which is
        # acceptable for a single-engineer dashboard and noted rather than
        # solved with a session-keyed registry.
        trace, lock = self._trace, self._trace_lock

        def note(name, **args):
            with lock:
                trace.append({"tool": name, "input": args})

        def get_fab_state() -> dict:
            """Current live fab state: ready/in-flight lot counts, completed
            total, throughput, and per-tool online status, plus the list of
            tools currently offline. Call this before answering anything about
            right now."""
            note("get_fab_state")
            snap = mirror.snapshot()
            offline = [t for t, v in snap["tools"].items() if not v["online"]]
            return _cap({**snap, "tools_offline": offline})

        def get_recent_events(limit: int = 40) -> dict:
            """Recent lot and tool events from the Kafka mirror, oldest first.
            Use to explain what just happened or spot a pattern.

            Args:
                limit: how many events to return, at most 100.
            """
            n = max(1, min(int(limit or 40), 100))
            note("get_recent_events", limit=n)
            with mirror.lock:
                return _cap({"events": list(mirror.events)[-n:]})

        def run_scenario(tools_down: list[str]) -> dict:
            """Run a what-if against a CLONED registry using the same C++
            planner the dispatcher uses. Takes the given tools offline and
            re-plans, returning a baseline-vs-scenario diff: which lots
            reroute, which become unassignable.

            Args:
                tools_down: tool IDs to take offline, e.g. ["LITHO_03"].
            """
            note("run_scenario", tools_down=list(tools_down or []))
            return _cap(planner(
                [{"tool_id": t, "online": False} for t in tools_down or []]))

        def explain_unassigned() -> dict:
            """For the current ready pool, list lots the planner could not
            assign, with the reason for each."""
            note("explain_unassigned")
            res = planner([])
            if "error" in res:
                return res
            body = res.get("scenario", res)
            return _cap({"unassigned": body.get("unassigned", []),
                         "assigned": body.get("assigned", 0)})

        return {"state": [get_fab_state, get_recent_events],
                "scenario": [run_scenario, explain_unassigned]}

    def _build_agents(self):
        tools = self._tools()
        cfg = gtypes.GenerateContentConfig(max_output_tokens=MAX_TOKENS,
                                           temperature=0.2)
        state = LlmAgent(
            name="state", model=MODEL, generate_content_config=cfg,
            description="Reports the fab's live state: WIP, tool status, "
                        "recent events, the bottleneck, why lots are waiting.",
            instruction=STATE_INSTRUCTION, tools=tools["state"],
        )
        scenario = LlmAgent(
            name="scenario", model=MODEL, generate_content_config=cfg,
            description="Runs what-if scenarios (tools down, re-plan) against "
                        "a cloned registry and explains unassigned lots.",
            instruction=SCENARIO_INSTRUCTION, tools=tools["scenario"],
        )
        # AgentTool rather than sub_agents: the root keeps the conversation and
        # may consult both specialists for one question, instead of handing the
        # whole turn over to one of them.
        return LlmAgent(
            name="dispatch", model=MODEL, generate_content_config=cfg,
            description="Dispatch assistant for the fab engineer.",
            instruction=ROOT_INSTRUCTION,
            tools=[AgentTool(state), AgentTool(scenario)],
        )

    # ---- conversation --------------------------------------------------------

    @staticmethod
    def describe_view(context):
        """One line for the root's instruction: which page, what is open."""
        c = context or {}
        tab = c.get("tab") or "live"
        bits = [f"the '{tab}' tab"]
        if c.get("openTool"):     bits.append(f"tool {c['openTool']} open")
        if c.get("openProduct"):  bits.append(f"product route {c['openProduct']} open")
        if c.get("cohort"):       bits.append(f"cohort {c['cohort']} selected")
        off = c.get("offline") or []
        bits.append(f"tools down: {', '.join(off[:8])}" if off else "no tools down")
        return "; ".join(bits)

    def _session_from(self, messages, context):
        """A fresh session seeded with every turn but the last user one, so
        the model sees the conversation the UI shows. The current view goes
        in session state, where the instruction's {view} placeholder reads it."""
        user_id = "ui"
        async def seed():
            session = await self.sessions.create_session(
                app_name=APP_NAME, user_id=user_id, session_id=uuid.uuid4().hex,
                state={"view": self.describe_view(context)})
            for m in messages[:-1]:
                is_user = m["role"] == "user"
                await self.sessions.append_event(session, Event(
                    author="user" if is_user else self.root.name,
                    invocation_id=uuid.uuid4().hex,
                    content=gtypes.Content(
                        role="user" if is_user else "model",
                        parts=[gtypes.Part.from_text(text=m["content"])])))
            return session
        return user_id, asyncio.run(seed())

    def ask(self, messages, context=None):
        """
        messages: [{"role":"user"|"assistant","content":str}, ...]
        context:  what the UI is showing: {tab, openTool, openProduct,
                  cohort, offline: [tool ids]}; all optional.
        Returns {"reply": str, "tools_used": [...], "error": str|None}
        Runs the agents to completion, then returns the final text.
        """
        if not self.available:
            return {"reply": None, "tools_used": [],
                    "error": self.error or "assistant unavailable"}
        if not messages or messages[-1]["role"] != "user":
            return {"reply": None, "tools_used": [],
                    "error": "last message must be from the user"}

        user_id, session = self._session_from(messages, context)
        with self._trace_lock:
            self._trace.clear()
        reply = None
        try:
            events = self.runner.run(
                user_id=user_id, session_id=session.id,
                new_message=gtypes.Content(
                    role="user",
                    parts=[gtypes.Part.from_text(text=messages[-1]["content"])]))
            for ev in events:
                if ev.error_message:
                    return {"reply": None, "tools_used": self._used(),
                            "error": ev.error_message}
                if ev.author == self.root.name and ev.is_final_response() and ev.content:
                    text = "".join(p.text or "" for p in ev.content.parts or [])
                    if text.strip():
                        reply = text
        except Exception as e:                           # noqa: BLE001
            return {"reply": None, "tools_used": self._used(), "error": str(e)}
        finally:
            try:
                self.sessions.delete_session_sync(
                    app_name=APP_NAME, user_id=user_id, session_id=session.id)
            except Exception:                            # noqa: BLE001
                pass

        if reply is None:
            return {"reply": None, "tools_used": self._used(),
                    "error": "the agent finished without a reply"}
        return {"reply": reply, "tools_used": self._used(), "error": None}

    def _used(self):
        with self._trace_lock:
            return list(self._trace)
