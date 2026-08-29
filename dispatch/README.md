# fabdisp — end-to-end fab dispatch skeleton

A runnable walking skeleton of the multi-horizon dispatch architecture. Every
layer is present and connected; pieces needing real infrastructure have working
fallbacks and are marked `PLACEHOLDER`.

## Closed-loop pipeline

```
[producer]    MES/AMHS stand-in ──> LOT_READY, TOOL_STATUS
                    │
[ingest]      SINGLE WRITER ──> FabState
                    │
[planner]     every N sec ──> AssignmentModel ──> SolverBackend ──> Slate
                    │                                    (atomic swap)
[dispatch]    move request ──> Slate lookup (~200ns) ──> decision topic
                    │
[equipment]   LOT_STARTED ──> (process time) ──> LOT_COMPLETE
                    │
                    └──────> back to ingest: frees tool capacity
```

## Build & run

```bash
# zero dependencies
g++ -std=c++20 -O2 -pthread -Iinclude src/e2e_main.cpp -o fabdisp
./fabdisp

# options
./fabdisp --config config/fab_tools.json --solver cpsat --brokers localhost:9092

# cmake, with any combination of backends
cmake -B build -DFAB_HAVE_RDKAFKA=ON -DFAB_HAVE_ORTOOLS=ON
cmake -B build -DFAB_HAVE_GUROBI=ON      # needs GUROBI_HOME + license server
cmake -B build -DFAB_HAVE_HIGHS=ON       # free MILP
```

## Solver backends

All four consume the identical `AssignmentModel` built from
`MachineConfiguration::evaluate()`, so swapping is one flag and the comparison
is apples-to-apples.

| `--solver` | Build flag | Notes |
|---|---|---|
| `greedy` | always | Cost-ordered. Timeout fallback + benchmark baseline. |
| `cpsat` | `FAB_HAVE_ORTOOLS` | Recommended for tactical. Native NoOverlap / Cumulative / AddCircuit. |
| `gurobi` | `FAB_HAVE_GUROBI` | Commercial MILP. Needs a license daemon in the fab zone. |
| `highs` | `FAB_HAVE_HIGHS` | Free MILP. Reads the LP text `SolverExporter::to_lp()` already emits. |

Unlinked backends fall back to greedy and say so at startup rather than
failing. That is deliberate: a dead license server must not stop the fab.

## Measured (demo run, 12 tools)

```
fast path      p50 200ns   p99 200ns   p99.9 800ns   (200k decisions)
40-veh burst   12.4us total, 0.3us/decision
closed loop    74 started = 72 completed + 2 in flight
integrity      0 malformed, 0 seq gaps, 0 unknown tools
```

## Files

| File | Role |
|---|---|
| `machine_config.hpp` | Tool class hierarchy, `SolverExporter` (model + LP) |
| `json.hpp` | Minimal JSON parser (→ nlohmann/simdjson) |
| `tool_factory.hpp` | Builder registry, config load, validation |
| `config/fab_tools.json` | Tool master |
| `events.hpp` | Wire schema, per-source sequencing |
| `transport.hpp` | `Producer`/`Consumer`; Kafka + in-memory |
| `state.hpp` | Single-writer `FabState` |
| `slate.hpp` | Immutable plan + atomic publish |
| `solver.hpp` | Pluggable backends |
| `planner.hpp` | Tactical layer, alternates, warm start |
| `dispatcher.hpp` | Fast path + latency histogram |
| `producer_sim.hpp` | Lot/tool event generator |
| `equipment_sim.hpp` | Closes the start/complete loop |

## Adding a machine configuration

1. Subclass `MachineConfiguration`, implement `evaluate` / `free_capacity` /
   `admit` / `release`.
2. `ToolFactory::register_kind("MY_KIND", builder)`.
3. Add entries to `fab_tools.json`.

Nothing in the planner, solver, or dispatcher changes.

## Placeholders, by priority

```bash
grep -rn PLACEHOLDER include/ src/
```

**Blocking**
1. `solver.hpp` — CP-SAT model body (the comment block is the spec)
2. `transport.hpp` — Kafka offset commit *after* apply, queue-full backpressure, SASL/SSL
3. `events.hpp` — FlatBuffers/Protobuf + schema registry
4. `state.hpp` — snapshot-copy so ingestion never blocks the solver; resync on seq gap
5. `tool_factory.hpp` — hot reload
6. `machine_config.hpp` — virtual `add_qual`/`remove_qual` for RECIPE_QUAL events

**Before anyone trusts it**
7. `dispatcher.hpp` — AMHS hold-queue escalation on `BothDown`
8. Decision log to `fab.dispatch.decisions` for replay
9. `equipment_sim.hpp` — process abort, rework, scrap, E10 states
10. Tests: per-configuration units, `evaluate()`/`admit()` property test, latency assertion

## Notes from the run

`start rejected` counts lots whose tool filled between planning and vehicle
arrival. `admit()` catches these — a stale decision is rejected at the tool, not
allowed to corrupt state. A persistently high number means the planning cycle is
too slow relative to the move rate.

`alternate failover` stays 0 in short runs because the producer's tool-down rate
is low. Raise `SimConfig::tool_down_chance` to exercise that path.

---

## Visualization layer (Zone 3)

Flask API + React dashboard, containerised. `make infra-up` then
<http://localhost:8080> — the only published port in the stack.

**Three views**

- **live** — WIP chart and event feed over Server-Sent Events
- **scenario** — click tools to take them down, re-plan, see the diff
- **topology** — the four zones and their enforced invariants, rendered from
  `zones.yaml` itself rather than hand-drawn

**The integrity rule.** `/api/scenario` shells out to the `fab_scenario` C++
binary, which instantiates a fresh `ToolRegistry` from the same config and runs
the same `Planner` and `SolverBackend` as the dispatcher. Dispatch logic is
never reimplemented in Python. A what-if answer that diverges from what the
dispatcher would actually do is worse than no answer.

**Read-only, enforced three ways:** nginx `limit_except` at the edge, a Flask
`before_request` guard, and consume-only Kafka config. The API produces to no
topic and opens no socket into Zone 1. There is no path from a browser to the
dispatcher.

```bash
make api    # Flask on :8000  (needs `make scenario` first)
make ui     # Vite dev server, proxies /api
```

**Verified working:** React builds (534 kB, 154 kB gzipped); Flask serves
health/zones/state/events/decisions/stream; SSE streams; POST to a non-scenario
path returns 403; and a two-tool-down scenario returns the correct diff —
two lots rerouted, one newly unassignable.

### Assistant (Claude on Vertex AI)

An `assistant` tab in the dashboard answers questions about live state and runs
what-ifs conversationally. Claude is served from **Google Vertex AI** via
`AnthropicVertex`, so the model call stays inside your GCP project.

**Grounding is the whole design.** The model is given four tools and is
instructed that every number, tool ID, and lot ID it states must come from a
tool result:

| tool | does |
|---|---|
| `get_fab_state` | live counts, throughput, per-tool online status |
| `get_recent_events` | recent lot/tool events from the Kafka mirror |
| `run_scenario` | takes tools down and re-plans via the **C++ planner** |
| `explain_unassigned` | held lots with the reason for each |

`run_scenario` invokes the same `fab_scenario` binary the scenario tab uses, so
the assistant's what-if answers cannot diverge from the dispatcher's actual
behaviour. A dispatch assistant that hallucinates a tool ID is worse than none,
which is why nothing here is answered from model recall.

The UI shows a trace chip above each reply naming the tools that ran, so an
engineer can see what the answer was built from.

**Config**

```bash
export GOOGLE_CLOUD_PROJECT=your-project
export VERTEX_REGION=us-east5
export GCP_SA_KEY=/path/to/sa.json     # >>> prefer Workload Identity in GKE
make infra-up
```

Without credentials the tab degrades to a clear "unavailable" message and the
rest of the dashboard is unaffected — verified.

**Zone note.** The assistant lives in the API (zone 2↔3 boundary) and is the
one component that reaches the internet, only because Vertex requires egress
and zone 3 permits it. It has no path to the dispatcher and cannot change the
fab; the read-only guard applies to it like everything else.

---

## Data transport, cold start, and historic data

Three gaps that were open and are now closed (or honestly scoped).

### 1. HSMS is real, not stubbed

`src/hsms_sim_main.cpp` is a genuine HSMS passive entity: real TCP, real
SEMI E37 framing, real SECS-II bodies. `include/fab/secs2.hpp` is a working
E5 item codec (L/A/B/U1/U2/U4, recursive, length-prefixed).

```bash
make hsms-test     # two processes, real socket
```

Measured: Select.req/rsp handshake completes, **43 S6F11 event reports decoded,
0 decode failures**, S2F41 TRANSFER commands encoded and acknowledged with
S2F42. The adapter reconnects after T5 rather than in a tight loop — a
reconnect storm against a struggling controller makes an outage worse.

What is still vendor-specific and must be confirmed:

- **CEIDs and report variable IDs.** Ours are in `hsms.hpp`. The real ones come
  from the controller's GEM compliance statement. Change them in that one file.
- **T3 reply timeout.** Drives your dispatch SLA.
- **Push vs poll.** If the controller is poll-only, the poll interval becomes
  the system's latency floor and the real-time design needs revisiting.
  **Ask this first.**

### 2. Cold start

`include/fab/bootstrap.hpp`. A dispatcher that is *wrong* is worse than one
that is *slow*, so the fast path returns Hold until state is trustworthy.

| phase | source | why |
|---|---|---|
| 1 TOOL_STATE | compacted `fab.tool.state` topic | O(tools), not O(history) — restart costs seconds |
| 2 WIP_SNAPSHOT | MES query | the event log carries *changes*; only a snapshot gives current membership |
| 3 RECONCILE | buffered live events past the watermark | avoids resurrecting lots the snapshot retired |
| 4 READY | — | dispatching begins |

Startup aborts if fewer than 80% of configured tools reported state, and names
the specific tools it never heard from.

### 3. Historic data — use SMT2020

**SMT2020** (Kopp, Hassoun, Kalir & Mönch, *IEEE Trans. Semiconductor Mfg*
33(4), 2020) is the current standard open benchmark. It supersedes SEMATECH's
**MIMAC** datasets (1995), which the SMT2020 authors note lack implementation
guidelines and are hard to reproduce.

- ~1,071–1,265 machines, 105 tool groups
- up to 583 process steps per product, 44 mask layers
- scheduled *and* unscheduled downtime modelled
- HV/LM and LV/HM scenarios, plus engineering-lot variants

Related: **SMAT2022** (`github.com/kwoo-lee/SMAT2022`) extends SMT2020 with the
AMHS detail our transport layer actually needs. **Minifab** is fine for unit
tests, too small for performance work.

```bash
python3 tools/load_smt2020.py --inspect path/to/smt2020.json   # check mapping first
python3 tools/load_smt2020.py path/to/smt2020.json --out config/fab_tools_smt2020.json
./fabdisp --config config/fab_tools_smt2020.json
```

Verified on a representative structure: 6 tool groups → **77 tools**, correctly
classified across all six machine kinds, loaded and dispatching.

The loader is tolerant and reports what it could not map rather than guessing —
SMT2020 has been repackaged several times, so run `--inspect` against your
actual download and check the classification before trusting the output.
Anything landing in `SINGLE_WAFER` that should batch will change your results.

**This is the scaling experiment worth running.** 1,265 tools is 100x the demo
config, and it is where the greedy fallback should visibly lose to CP-SAT.

---

## The CP-SAT model — and what benchmarking it found

`include/fab/solver.hpp` now contains the real CP-SAT model, not a comment
block. `tools/cpsat_bench.py` is the same model in Python, run against the same
exported JSON, so the formulation is validated by two independent
implementations. Any disagreement is a formulation bug.

### Results (200 lots × 60 tools, 859 feasible pairs)

```
            assigned    objective     solve   status
greedy            69      6,984,848      0.4ms  GREEDY
cpsat             80      4,584,282   5005.9ms  OPTIMAL

cost reduction vs greedy: +34.4%      lots assigned: +11
constraint violations:    both 0
```

Scaling sweep, 5s budget — CP-SAT stays optimal to 800 lots:

| lots | tools | pairs | greedy | cpsat | lift |
|---|---|---|---|---|---|
| 50 | 20 | 205 | 0ms | 2.9s | +44.8% |
| 200 | 50 | 842 | 0ms | 5.0s | +23.2% |
| 800 | 200 | 3,367 | 2ms | 5.0s | +28.5% |

### Three bugs the benchmark found

**1. Greedy violated reticle exclusivity.** It reported 78 lots assigned with
one reticle on five scanners at once. The count was fiction — the assignments
were physically impossible. Fixed in both `GreedySolver::solve()` and the
Python baseline. *Any dispatcher benchmark without a constraint audit will
flatter a heuristic that cheats.*

**2. The objective paid the fab to do nothing.** With a fixed unassignment
penalty, running a lot cost up to 4.4× more than holding it. CP-SAT returned
OPTIMAL, every constraint satisfied, 30 of 200 lots assigned — correct solver,
wrong objective. The penalty is now anchored to `max_cost`, so "run it
somewhere" always beats "hold it" and urgency only orders *which* lots win when
capacity is short. Nothing in the type system would have caught this.

**3. The comparison was apples-to-oranges.** Greedy was scored on assignment
cost alone, which rewarded it for assigning fewer lots. Both solvers now score
through one `score()` function.

### The operational number you need

At 400 lots, a **0.25s budget returns no incumbent at all** — CP-SAT falls back
to greedy. At 1s it is optimal.

```
budget 0.25s   cpsat UNKNOWN, 0 assigned  -> fallback fires
budget 1.0s    cpsat OPTIMAL, +31.7% vs greedy
```

So the tactical cycle must allow **≥1s of solve time at this scale**, and the
greedy fallback is load-bearing rather than decorative. That is a concrete
tuning constraint, and it only exists because the experiment was run.

### Still true

The C++ CP-SAT path compiles but has **not been linked against OR-Tools** here.
The Python implementation is what produced every number above. Building the C++
with `-DFAB_HAVE_ORTOOLS` and confirming it reproduces these results is the
next step, and the JSON export exists precisely so that comparison is exact.
