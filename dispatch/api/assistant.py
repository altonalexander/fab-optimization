# api/assistant.py — Gemini (Google ADK) on Vertex AI, grounded in live fab
# state and the repo README.
#
# ZONE 3 boundary component. Same read-only rule as the rest of the API: the
# assistant can READ live state, READ any page's data, and RUN SCENARIOS
# against a cloned registry. It has no path to the dispatcher and cannot
# change the fab.
#
# Two kinds of question, two sources:
#   "how does this page work"      -> the README, held in the system prompt.
#                                     No tool call: one model round trip.
#   "what is this page showing me" -> a tool call to the page's own API
#                                     endpoint (get_page_data), or to live
#                                     state / the planner. Two round trips.
#
# Latency is the design constraint. One agent, no sub-agents: every extra
# agent is another model call. Thinking is off: Flash answers a grounded
# question in well under a second without it and ~2 s slower with it. The
# system prompt is static (README included) and long enough that Vertex's
# implicit prompt cache serves it from the second request on; the only
# per-request text is the one-line view description at the very end.
#
# Runs in-process: the ADK Runner is driven synchronously per request over an
# in-memory session rebuilt from the transcript the UI sends. Nothing persists
# server-side, so the API stays stateless like every other endpoint.

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
    from google.adk.models.google_llm import Gemini
    from google import genai
    from google.genai import types as gtypes
    _SDK = True
except ImportError:
    _SDK = False

MODEL       = os.getenv("VERTEX_MODEL", "gemini-2.5-flash")
REGION      = os.getenv("VERTEX_REGION", "us-central1")
PROJECT     = os.getenv("GOOGLE_CLOUD_PROJECT", "")
MAX_TOKENS  = int(os.getenv("ASSISTANT_MAX_TOKENS", "1200"))
# Thinking budget in tokens; 0 is off. Leave off unless answers get sloppy.
THINKING    = int(os.getenv("ASSISTANT_THINKING", "0"))
APP_NAME    = "fab-dispatch"
_HERE       = os.path.dirname(os.path.abspath(__file__))
# The repo README is the help guide. Resolved relative to this file so the
# dev box finds it; the container sets README_PATH (or ships without one and
# the agent says so rather than guessing).
README_PATH = os.getenv("README_PATH", os.path.join(_HERE, "..", "..", "README.md"))


def _adc_project():
    """Project recorded with the local ADC, or '' when there is none."""
    try:
        import google.auth
        _, proj = google.auth.default()
        return proj or ""
    except Exception:                                   # noqa: BLE001
        return ""


def _read_readme():
    try:
        with open(README_PATH, encoding="utf-8") as f:
            return f.read()
    except OSError:
        return ""


# Which API endpoint backs which page, for get_page_data. Only GET endpoints
# that already serve the dashboard; nothing here can write.
READABLE = ("/api/state", "/api/events", "/api/kpi", "/api/lots", "/api/tools",
            "/api/layout", "/api/routes", "/api/slate/status", "/api/slate/compare",
            "/api/runs", "/api/zones", "/api/decisions")
PAGE_DATA = """PAGE -> DATA ENDPOINT (for get_page_data):
- #/live                 /api/state (counts, per-tool status, KPIs, sim clock)
                         /api/events?limit=N (recent feed), /api/kpi (history)
- #/lots                 /api/lots (cohort index), /api/lots/hot (lots to watch)
- #/lots?cohort=ID       /api/lots/ID (that cohort's lots, journeys, burndown)
- #/tools                /api/tools (index: queues, status, groups),
                         /api/tools/availability (roster over time)
- #/tools/ID             /api/tools/ID (that tool's decisions, setups, lots)
- #/floor                /api/layout (bays and positions), /api/layout/state
                         (what is on each position now)
- #/routes               /api/routes (products), #/routes/P -> /api/routes/P
- #/slate                /api/slate/status, /api/slate/compare
- #/results              /api/runs (all runs), /api/runs/N/kpi, /api/runs/N/tools
- #/topology             /api/zones
Always pass the path exactly as listed, e.g. get_page_data("/api/tools/ETCH_11")."""

LINKS = """LINKING: the dashboard is hash-routed. When a reply names something that
has a page, link it in markdown so the engineer can jump there:
- a tool:     [ETCH_11](#/tools/ETCH_11)
- a cohort:   [part_3-d-11](#/lots?cohort=part_3-d-11)
- a product:  [part_3](#/routes/part_3)
- a bay:      [bay 3,2](#/floor?bay=3,2)
- pages:      [tools](#/tools) [floor](#/floor) [lots](#/lots) [routes](#/routes)
              [slate](#/slate) [results](#/results) [topology](#/topology) [live](#/live)
Link only things the tools or the README actually named. One link per item,
inline, never a bare URL."""

GROUNDING = """GROUNDING RULES — absolute:
- Every number, tool ID, lot ID, and recipe you state must come from a tool
  result in this conversation. Never recall or invent one.
- If you don't have the data, call a tool. If a tool can't get it, say so
  plainly rather than estimating.
- When you don't know why something happened, say you don't know and name
  what data would answer it.
- You are READ-ONLY. You can read pages, live state and run scenarios against
  a cloned registry. You cannot change the fab; say so if asked to."""

DOMAIN = """DOMAIN CONTEXT you may reason from:
- Tool kinds: SINGLE_WAFER (one lot, recipe-change setup), BATCH_FURNACE
  (fixed process time, needs a minimum batch), CLUSTER (per-chamber
  qualification, parallel), LITHO_SCANNER (reticle is exclusive), METROLOGY
  (sampled; skipping is valid), PROBE_TESTER (probe card must match product).
- Common reasons a lot is unassigned: no qualified tool, no free capacity,
  batch below minimum, reticle held elsewhere, no matching probe card.
- Delay_* stations are queue-time placeholders, not equipment."""

INSTRUCTION_HEAD = """You are the assistant built into the Fab Optimization dashboard, a
read-only view of a simulated 300mm wafer fab. You help the engineer using it.

HOW TO ANSWER
1. "How does this page work / what can I do here / what does this chart
   mean / where do I find X": answer from the README below. No tool call.
   Be specific to the page they are on unless they ask about another.
2. "What is this page showing / interpret this / what is happening / which
   tool, lot, cohort ...": call get_page_data on the page's endpoint (table
   below), or get_fab_state for the whole fab, then answer from the result.
3. "What if X goes down" -> run_scenario. "Why are lots unassigned/held" ->
   explain_unassigned. Only the planner can answer those.
4. "Which tool is the bottleneck / where is WIP piling up / what is slowing
   the fab / busiest area": call get_bottlenecks. It ranks tool categories
   (groups) by the WIP queued at them right now, the same numbers as the
   floor page's heatmap, plus the busiest bays. Report the top three unless
   they ask for a specific number, tool type, or bay. Link each group as
   [GROUP](#/tools?type=GROUP) and each bay as [bay B,S](#/floor?bay=B,S).
Call at most the tools you need, in one round when possible. Then answer.
If a tool returns an error naming valid paths, call it again with the right
path before answering; never answer from an error alone.

STYLE: concise and direct. Lead with the answer. An engineer is reading this
mid-shift. Short list over paragraph. No preamble, no restating the question.
Markdown: bold sparingly, lists, and links as below.

""" + LINKS + "\n\n" + PAGE_DATA + "\n\n" + GROUNDING + "\n\n" + DOMAIN


def _instruction():
    readme = _read_readme()
    guide = ("README (the help guide for the whole app; the 'tour of the "
             "dashboard' section describes each page):\n\n" + readme) if readme \
        else "README: not available in this deployment; answer app questions " \
             "from the page table above and say the guide is missing."
    return INSTRUCTION_HEAD + "\n\n" + guide


def _shrink(obj, max_items=40, max_str=240, depth=0):
    """Trim a page payload to what a model needs: long lists become their
    head plus a count, long strings are cut. Structure is preserved so the
    model still sees the field names."""
    if isinstance(obj, dict):
        return {k: _shrink(v, max_items, max_str, depth + 1) for k, v in obj.items()}
    if isinstance(obj, list):
        if len(obj) > max_items:
            head = [_shrink(v, max_items, max_str, depth + 1) for v in obj[:max_items]]
            return head + [f"... {len(obj) - max_items} more items omitted"]
        return [_shrink(v, max_items, max_str, depth + 1) for v in obj]
    if isinstance(obj, str) and len(obj) > max_str:
        return obj[:max_str] + "…"
    return obj


def _cap(obj, limit=16000):
    """Bound what a tool hands back to the model so one payload cannot blow
    the context or the latency."""
    obj = _shrink(obj)
    s = json.dumps(obj)
    if len(s) <= limit:
        return obj
    obj = _shrink(obj, max_items=12, max_str=120)
    s = json.dumps(obj)
    return obj if len(s) <= limit else {"truncated": True, "data": s[:limit]}


class FabAssistant:
    """One agent with five read-only tools, bound to the live mirror, the
    planner, and the API's own GET endpoints."""

    def __init__(self, mirror, scenario_runner, local_get=None):
        self.mirror = mirror
        self.run_scenario = scenario_runner
        # local_get(path) -> (status, json|text): the API calling itself, so
        # get_page_data returns exactly what the page fetched.
        self.local_get = local_get
        self.runner = None
        self.error = None
        self._trace, self._trace_lock = [], threading.Lock()
        self.project = PROJECT or _adc_project()
        self.readme_loaded = bool(_read_readme())
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
                # One client for the process. Given the model as a string,
                # ADK builds a client per request, and each build runs
                # google-auth's Cloud SDK probe (`gcloud config config-helper`,
                # ~18 s on a WSL box with the Windows SDK). Built here once,
                # with the project passed explicitly, so no request pays it.
                self._client = genai.Client(vertexai=True, project=self.project,
                                            location=REGION)
                self.root = self._build_agent()
                self.sessions = InMemorySessionService()
                self.runner = Runner(app_name=APP_NAME, agent=self.root,
                                     session_service=self.sessions)
                # The first request would otherwise pay the credential probe
                # and the first token fetch (tens of seconds on some boxes).
                # Pay it now, off the request path; a failure here just
                # means the first user request pays it instead.
                threading.Thread(target=self._warm, name="assistant-warm",
                                 daemon=True).start()
            except Exception as e:                      # noqa: BLE001
                self.error = f"agent init failed: {e}"

    def _warm(self):
        try:
            self._client.models.count_tokens(model=MODEL, contents="warm")
        except Exception:                                # noqa: BLE001
            pass

    @property
    def available(self):
        return self.runner is not None

    # ---- tools ---------------------------------------------------------------
    # Plain functions: ADK derives each declaration from the signature and
    # docstring, which is what the model sees. Keep the docstrings honest.

    def _tools(self):
        mirror, planner, local_get = self.mirror, self.run_scenario, self.local_get
        trace, lock = self._trace, self._trace_lock

        def note(name, **args):
            with lock:
                trace.append({"tool": name, "input": args})

        def get_fab_state() -> dict:
            """Whole-fab live state: ready/in-flight lot counts, completed
            total, KPIs, sim clock, per-tool online status, and the list of
            tools currently offline. Use for "what is the fab doing" and
            "which tools are down"."""
            note("get_fab_state")
            snap = mirror.snapshot()
            offline = [t for t, v in snap["tools"].items() if not v["online"]]
            return _cap({**snap, "tools_offline": offline})

        def get_page_data(path: str) -> dict:
            """Read the data behind a dashboard page from the API's own GET
            endpoint, e.g. "/api/tools/ETCH_11" for a tool page or
            "/api/lots/part_3-d-11" for a cohort. Use the PAGE -> DATA table
            to pick the path. Read-only. Large lists come back trimmed to
            their first items plus a count.

            Args:
                path: the endpoint path starting with /api/, as in the table.
            """
            note("get_page_data", path=path)
            p = (path or "").strip()
            # Common guesses, mapped rather than refused.
            p = {"/api/floor": "/api/layout/state", "/api/floor/state": "/api/layout/state",
                 "/api/cohorts": "/api/lots", "/api/products": "/api/routes",
                 "/api/tool": "/api/tools", "/api/live": "/api/state"}.get(p, p)
            if p.startswith("/api/tool/"):
                p = "/api/tools/" + p[len("/api/tool/"):]
            base = p.split("?", 1)[0].rstrip("/")
            ok = any(base == r or base.startswith(r + "/") for r in READABLE)
            if not ok:
                return {"error": f"{p} is not a page endpoint",
                        "use_one_of": list(READABLE),
                        "hint": "see the PAGE -> DATA table; e.g. /api/layout/state for the floor"}
            if local_get is None:
                return {"error": "page data is not wired up in this deployment"}
            status, body = local_get(p)
            if status != 200:
                return {"error": f"{p} returned {status}", "body": str(body)[:300]}
            return _cap(body)

        def get_bottlenecks(n: int = 3) -> dict:
            """Where WIP is piling up right now: tool categories (groups)
            ranked by the lots queued at them, with each group's busiest
            tools, plus the busiest floor bays. Same numbers as the floor
            page's WIP heatmap. Use for "which tool is the bottleneck",
            "what is slowing the fab", "busiest area".

            Args:
                n: how many groups and bays to return (default 3, max 10).
            """
            n = max(1, min(int(n or 3), 10))
            note("get_bottlenecks", n=n)
            if local_get is None:
                return {"error": "page data is not wired up in this deployment"}
            st, tools = local_get("/api/tools")
            if st != 200 or not isinstance(tools, dict):
                return {"error": f"/api/tools returned {st}"}
            groups = []
            for g in tools.get("groups", []):
                # Delay_* groups are route-prescribed waits, not equipment:
                # lots sit there by design, so they are never the bottleneck.
                if str(g.get("group", "")).startswith("Delay_"):
                    continue
                rows = g.get("tools") or []
                q = lambda r: r.get("queue") if r.get("queue") is not None else r.get("waiting_count", 0) or 0
                wip = sum(q(r) for r in rows)
                busiest = sorted(rows, key=q, reverse=True)[:3]
                groups.append({
                    "group": g.get("group"), "wip_queued": wip,
                    "tools": g.get("count", len(rows)), "offline": g.get("offline", 0),
                    "queue_max": g.get("queue_max"),
                    "running": sum(r.get("running_count", 0) or 0 for r in rows),
                    "busiest_tools": [{"id": r.get("id"), "queue": q(r),
                                       "online": r.get("online")} for r in busiest],
                })
            groups.sort(key=lambda x: x["wip_queued"], reverse=True)
            out = {"ranked_by": "lots queued at the tool group right now "
                                "(Delay_* queue-time placeholders excluded)",
                   "top_groups": groups[:n],
                   "total_queued": sum(x["wip_queued"] for x in groups)}
            st, lay = local_get("/api/layout/state")
            if st == 200 and isinstance(lay, dict):
                cells = sorted(lay.get("cells", []), key=lambda c: c.get("wip", 0), reverse=True)
                out["top_bays"] = [{"bay": f"{c['bay']},{c['seg']}", "wip": c.get("wip"),
                                    "queue_max": c.get("queue_max"), "tools": c.get("tools"),
                                    "down": c.get("down")} for c in cells[:n]]
            return out

        def get_recent_events(limit: int = 40) -> dict:
            """Recent lot and tool events from the live feed, oldest first.
            Use to explain what just happened.

            Args:
                limit: how many events to return, at most 100.
            """
            n = max(1, min(int(limit or 40), 100))
            note("get_recent_events", limit=n)
            with mirror.lock:
                return _cap({"events": list(mirror.events)[-n:]})

        def run_scenario(tools_down: list[str]) -> dict:
            """What-if against a CLONED registry using the same C++ planner
            the dispatcher uses: takes the given tools offline, re-plans, and
            returns a baseline-vs-scenario diff (which lots reroute, which
            become unassignable). Nothing in the real fab changes.

            Args:
                tools_down: tool IDs to take offline, e.g. ["LITHO_03"].
            """
            note("run_scenario", tools_down=list(tools_down or []))
            return _cap(planner([{"tool_id": t, "online": False} for t in tools_down or []]))

        def explain_unassigned() -> dict:
            """For the current ready pool, the lots the planner could not
            assign, with the reason for each."""
            note("explain_unassigned")
            res = planner([])
            if "error" in res:
                return res
            body = res.get("scenario", res)
            return _cap({"unassigned": body.get("unassigned", []),
                         "assigned": body.get("assigned", 0)})

        return [get_fab_state, get_page_data, get_bottlenecks, get_recent_events,
                run_scenario, explain_unassigned]

    def _build_agent(self):
        # A callable instruction rather than a string: ADK would otherwise
        # template `{...}` in the README as state placeholders. The static
        # text is built once; the view line is appended last so everything
        # before it is a byte-stable prefix for the prompt cache.
        static = _instruction()

        def instruction(ctx):
            view = (ctx.session.state or {}).get("view", "the dashboard")
            return static + "\n\nCURRENT VIEW: the engineer is looking at " + view

        cfg = gtypes.GenerateContentConfig(
            max_output_tokens=MAX_TOKENS, temperature=0.2,
            thinking_config=gtypes.ThinkingConfig(thinking_budget=THINKING))
        return LlmAgent(
            name="dispatch", model=Gemini(model=MODEL, client=self._client),
            generate_content_config=cfg,
            description="Assistant built into the Fab Optimization dashboard.",
            instruction=instruction, tools=self._tools(),
        )

    # ---- conversation --------------------------------------------------------

    @staticmethod
    def describe_view(context):
        """One line for the instruction: which page, what is open, the URL."""
        c = context or {}
        tab = c.get("tab") or "live"
        bits = [f"the '{tab}' tab"]
        if c.get("url"):          bits.append(f"URL {c['url']}")
        if c.get("openTool"):     bits.append(f"tool {c['openTool']} open")
        if c.get("openProduct"):  bits.append(f"product route {c['openProduct']} open")
        if c.get("cohort"):       bits.append(f"cohort {c['cohort']} selected")
        off = c.get("offline") or []
        bits.append(f"tools down now: {', '.join(off[:8])}" if off else "no tools down now")
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

    def _used(self):
        with self._trace_lock:
            return list(self._trace)

    def ask(self, messages, context=None):
        """
        messages: [{"role":"user"|"assistant","content":str}, ...]
        context:  what the UI is showing: {tab, url, openTool, openProduct,
                  cohort, offline: [tool ids]}; all optional.
        Returns {"reply": str, "tools_used": [...], "error": str|None}
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
