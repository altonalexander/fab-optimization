# 0018 — The dashboard assistant: one agent, two paths, nothing recalled

**Status:** Implemented, 2026-09-12. Records a shape that was already built and
had drifted from its own description — `dispatch/README.md` documented a
multi-agent router that the code had replaced with a single agent for latency,
and said four tools where there are six.

Reference for the endpoint lives beside the code (`dispatch/api/assistant.py`,
`dispatch/README.md`). This is the part that crosses: the assistant reads the
same live state the dispatcher acts on, answers from the same docs a human
reads, and is bounded by the same zone rule as the rest of the API.

---

## 1. What it has to do

An engineer mid-shift asks the dashboard a question. Two kinds, and they want
very different machinery:

- **"How does this page work?"** — *what does this chart mean, where do I find
  X, what can I do here.* The answer already exists, written down, in the
  project's own documentation.
- **"What is this page showing me?"** — *which tool is the bottleneck, why is
  this lot unassigned, what happens if LITHO_03 goes down.* The answer exists
  only in live state, or does not exist yet and has to be computed.

Conflating them is the obvious mistake: routing a documentation question
through a tool call doubles its latency for nothing, and answering a live
question from documentation invents numbers.

## 2. The shape

```
 browser · zone 4
   │  POST /api/chat
   │  { messages[], context{ tab, url, openTool, openProduct, cohort, offline[] } }
   ▼
 api · zone 3 · read-only
   │
   ├─ system prompt ─ STATIC ─► README.md + docs/dashboard.md + docs/running.md
   │                            (Vertex implicit cache serves it from req 2 on)
   └─ per request ──────────► one line: which tab, what is open, what is down
        │
        ▼
 ┌──────────── ONE LlmAgent · Gemini Flash · Vertex AI · thinking off ─────────┐
 │                                                                            │
 │  "how does this page work?"    ──► answer from the guide         1 round    │
 │                                                                trip        │
 │  "what is this page showing?"  ──► get_page_data(path)                      │
 │  "which tool is the bottleneck?"─► get_bottlenecks(n)                       │
 │  "is the sim paused?"          ──► get_fab_state()               2 round    │
 │  "what just happened?"         ──► get_recent_events(limit)      trips      │
 │  "what if LITHO_03 goes down?" ──► run_scenario(tools_down)                 │
 │  "why is this lot unassigned?" ──► explain_unassigned()                     │
 └────────────────────────────┬───────────────────────────────────────────────┘
                              │ READ ONLY — no path to the dispatcher
                              ▼
        Kafka mirror  ·  the page's own GET endpoint  ·  cloned registry
        (live state)     (the same JSON the page drew)   + the C++ planner
                                                           (what-ifs)
   │
   ▼
 reply, in markdown, with deep links into the hash router:
   [ETCH_11](#/tools/ETCH_11)   [part_3](#/routes/part_3)   [bay 3,2](#/floor?bay=3,2)
 plus a trace chip naming every tool that ran.
```

## 3. The decisions, and why

### 3.1 One agent, not a router with specialists

The earlier shape was a root agent that called a `state` specialist and a
`scenario` specialist as tools. It reads well and it is the wrong trade here:
**every extra agent is another model round trip**, and this thing is read by
someone standing at a tool.

One agent with six tools costs one hop for a documentation question and two
for a live one. The router cost two and three. The separation the specialists
bought — that a live number and a simulated one never come from the same agent
— is better served by §3.4, which forbids stating any number that did not come
from a tool result, whichever tool that was.

### 3.2 Thinking off

Measured: Flash answers a grounded question in well under a second without it,
and about 2 s slower with it. A grounded answer is mostly retrieval and
formatting; there is little to reason about once the tool result is in hand.
`ASSISTANT_THINKING` turns it back on if answers get sloppy — that is the
signal to watch, not a preference.

### 3.3 The system prompt is static, and the view is one line

The whole help corpus goes in the system prompt, unchanged between requests,
so Vertex's implicit prompt cache serves it from the second request onward.
The only per-request text is a single line at the very end describing the
current view — which tab, which tool or cohort is open, what is down right
now.

This is why the "how does this page work" path needs no tool call at all: the
documentation is *already in the context window*. It is also the constraint
that makes the corpus a load-bearing dependency rather than a nicety — see
§5.

### 3.4 Grounding is absolute, and it is a rule not a hope

Every number, tool ID, lot ID and recipe in a reply must come from a tool
result in that conversation. Never recalled, never estimated. If the data is
not available the agent says so and names what would answer it.

**A dispatch assistant that invents a tool ID is worse than no assistant**,
because it is confidently wrong about the one thing the engineer cannot check
at a glance. The trace chip above each reply, naming the tools that ran, is
the visible half of the same commitment.

### 3.5 Read-only by construction, not by instruction

The assistant sits in zone 3 under the same rule as every other API
component. It can read live state, read any page's data, and run a scenario
against a **cloned** registry. It has no path to the dispatcher. `run_scenario`
invokes the same planner the scenario tab does, so a what-if answer cannot
diverge from what the dispatcher would actually do.

The read-only property is a property of what is wired up, not of what the
prompt asks for.

### 3.6 Answers are navigable

The dashboard is hash-routed, so a named thing has a page. Replies link them
inline — a tool, a cohort, a product, a bay — which turns an answer into a
place to go rather than a paragraph to act on. Only things a tool or the guide
actually named may be linked, for the same reason as §3.4.

### 3.7 Stateless, like everything else in the API

Nothing persists server-side. The UI sends the transcript; the runner rebuilds
an in-memory session from it per request. The API stays as restartable as the
rest of zone 3.

## 4. What this assumes

- **Flash is good enough grounded.** The design gives the model little to do
  beyond choosing a tool and formatting a result. If answer quality is the
  binding constraint rather than latency, §3.1 and §3.2 are the first two
  things to revisit, in that order.
- **The implicit cache actually fires.** It is inferred from latency, not
  billed line items. If the corpus grows past what the cache will hold, the
  static-prompt trade quietly becomes a per-request cost.
- **The page → endpoint table stays true.** `get_page_data` maps a route to
  the GET endpoint that already backs it. A new page with no entry is
  invisible to the assistant, and nothing fails loudly when that happens.

## 5. The dependency that is easy to break

**The help corpus is the fast path.** §3.3 works only because the
documentation is in the prompt, so anything that moves documentation out of
the loaded files silently degrades every "how does this page work" answer —
with no error, and no test that would notice.

This happened the day this ADR was written. The README was split for human
readers (987 lines → 599, with the page-by-page tour moved to
`docs/dashboard.md` and operations to `docs/running.md`), and the assistant
loaded only `README.md`. The tour — the single most useful document for the
questions path 1 exists to serve — stopped being visible to it.

The fix is that the assistant loads a **corpus**, not a file: README plus the
docs split out of it, each under a `=== filename ===` heading so the model can
tell the reader which document to open. `ASSISTANT_HELP_PATHS` overrides for a
deployment that ships them elsewhere.

The general point is worth keeping: **the README has two audiences with
opposite preferences.** A human wants it short. The assistant wants everything.
Splitting for the human is right, and it is only safe if the corpus follows.

## 6. How to know whether this is right

- **Latency, split by path.** A documentation question should be one round
  trip and feel instant; a live question two. If path 1 starts calling tools,
  the guide has stopped covering the question being asked.
- **Trace chips on documentation questions.** A tool call on a "how does this
  work" question is the visible symptom of §5 having broken again.
- **Any ungrounded number, ever.** One invented tool ID is a defect, not a
  regression in quality — §3.4 is binary.

## 7. What would overturn it

- **Answers are wrong rather than slow.** Then latency was the wrong thing to
  optimise: turn thinking on, and if that is not enough the specialist split
  of §3.1 earns its round trip after all.
- **The corpus outgrows the cache.** Then the guide has to be retrieved rather
  than resident, which is a different design — a retrieval step in front of
  path 1 — and a different ADR.
- **Someone needs it to write.** Everything here rests on read-only; a write
  path is not an extension of this design, it is a replacement for it, and it
  would have to answer the zone question first.
