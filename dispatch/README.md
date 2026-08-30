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
| `config/fab_tools.json` | Tool master — symlink to `fab_tools_lvhm.json` |
| `events.hpp` | Wire schema, per-source sequencing |
| `transport.hpp` | `Producer`/`Consumer`; Kafka + in-memory |
| `state.hpp` | Single-writer `FabState` |
| `slate.hpp` | Immutable plan + atomic publish |
| `solver.hpp` | Pluggable backends |
| `planner.hpp` | Tactical layer, alternates, warm start |
| `dispatcher.hpp` | Fast path + latency histogram |
| `producer_sim.hpp` | Lot/tool event generator |
| `equipment_sim.hpp` | Closes the start/complete loop |

## Tool availability and recovery

The tool index opens with an availability strip: online tools over time, with
the roster drawn as a dashed reference line above it. The series should be
sitting just under the line; the gap is the outage.

It exists because the roster used to decay to nothing and nobody could see it
happening. `TOOL_STATUS` was a one-way street:

- PySCFabSim fires `on_breakdown` / `on_preventive_maintenance` when an outage
  *starts*. There is no end-of-outage hook — `BreakdownEvent.handle()` just
  shifts the machine's pending events forward by the sampled length
  (`simulation/events.py:71`, `instance.py:260`).
- So `sim_feed.py` emitted `online=0` and never `online=1`, and the mirror,
  which trusts the feed absolutely, only ever removed tools from service.

Over a 3-day LVHM run that read **75.8% online and still falling** — 318 of
1,313 tools stranded, none of which had actually failed. Extrapolated, the
dashboard shows a dead fab inside a fortnight.

Three guards now, in order of how much they are trusted:

1. **The feed closes its own loop.** `_tool_down` records when the tool is due
   back and `_recover_due` emits the matching `online=1`. The outage length is
   read from the simulator's own `bred_time` / `pmed_time` counters, which
   `handle()` increments *before* calling the plugin — resampling
   `event.length` instead would schedule a recovery unrelated to the outage the
   simulator is actually running. Overlapping outages extend rather than
   double-recover.
2. **Activity beats status.** A tool that starts a lot is running, whatever its
   last status event said, so the mirror marks it back up. This alone holds the
   roster at 97.6% on a feed that emits *zero* recoveries.
3. **Nothing stays down forever.** Past `TOOL_DOWN_TTL_S` (default 900s wall
   clock) with no word either way, the watchdog assumes up. This is the one
   that catches a dropped partition or a `--tool-prefix` filter.

(2) and (3) are inferences, not observations, so a tool restored by either is
tagged `recovered_by` and the count is shown in the strip. A number that climbs
there means the feed is lying and the mirror is papering over it — that is a
bug to chase, not a healthy steady state.

After: **97.8% online, low water 96.7%**, and the outage books balance — every
down period either closed or is still open at the run horizon.

Guarded by `scripts/smoke.sh` (`tool recovery` and `/api/tools/availability`),
which fails on the old one-way feed. `dispatch/api/t_feed_recovery.py <feed>`
checks the producer; `t_watchdog_replay.py <feed>` strips the recoveries back
out and checks the mirror holds the roster without them.

## The lots view (cohort burndown)

Remaining route steps per lot against simulated time, one line per lot,
descending to zero at completion.

```
sim_feed.py  --(LOT_PROGRESS on fab.lot.burndown)-->  api  -->  /api/lots
```

`steps_remaining` is computed in the feed from the lot's route and passed
through untouched; the browser never sees a route. Three properties are
deliberate and easy to break:

- **It is not monotonic.** Rework splices already-processed steps back onto the
  front of the route (`simulation/instance.py:122`), so the line goes *up*.
  Nothing clamps it. Roughly 3 jogs per 2 simulated days in LVHM.
- **It counts route positions, not distinct operations.** A lot with 40 steps
  left may visit litho six more times; deduplicating by operation name would
  understate the work remaining.
- **Flat runs are attributed.** The simulator tracks `waiting_time_batching`
  separately from general queueing, so a horizontal run can be labelled
  *waiting on cohort* from measurement rather than inference.

A **cohort** is not an SMT2020 concept, so `--cohort-mode` defines it:

| mode | grouping | use |
|---|---|---|
| `product-day` (default) | one product's releases within one day | the lots that can actually batch — a furnace batch needs the same product *and* the same step. ~5.6 lots in LVHM, about one batch worth |
| `release-wave` | lots released at the same instant | in LVHM that is one lot of each of 10 products, which can **never** batch with each other. Useful for watching identically-released lots diverge; misleading if read as batch partners |

Endpoints:

| Route | Returns |
|---|---|
| `GET /api/lots` | cohort index, ranked by last movement, with min/median/max steps left and the spread |
| `GET /api/lots/<cohort>` | per-lot series for one cohort |

Points are held in one bounded ring (`BURNDOWN_MAX`, default 150k) rather than
per-lot series with an eviction policy: LVHM emits ~23k progress events per
simulated day across ~2k lots in flight, so per-lot retention either leaks or
silently drops the lots you were watching.

Geometry lives in `ui/src/burndown_geom.js`, free of pixel scales and of React,
and is checked against a live API:

```bash
cd dispatch/ui && node src/burndown_geom.test.mjs http://localhost:8000
```

`--no-burndown` on `sim_feed.py` turns the events off and roughly halves lot
event volume; the lots view then reports that it has no points.

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

**Use `tools/smt2020_tool_master.py` for the data in `data/smt2020/`.** It reads
the authors' tab-separated distribution, which is what this repo ships, and
derives each tool's kind from behaviour in the data (BATCHMN/BATCHMX for
furnaces, StepPercent for sampled metrology) rather than from keywords in its
name. `load_smt2020.py` below expects a JSON repackaging we do not have, and its
keyword rules match none of SMT2020's abbreviated family names — it would
silently classify the whole fab as SINGLE_WAFER.

```bash
python3 tools/smt2020_tool_master.py --scenario LVHM   # -> config/fab_tools_lvhm.json
```

Produces **913 tools across 105 station families** for LVHM: 10 BATCH_FURNACE
(Diffusion), 13 METROLOGY (the *_Met areas), 11 LITHO_SCANNER, 71 SINGLE_WAFER.
SMT2020's 400 `Delay_*` queue-time pseudo-tools are excluded, as `tool_probe.py`
excludes them. Read the script's header before trusting the numbers: reticles,
the setup matrix, and per-step process times are all approximated there.

For a JSON repackaging of SMT2020 from elsewhere:

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

At 400 lots, measured against the linked C++ build:

```
budget 0.25s   no incumbent -> fallback to greedy
budget 1.0s    no incumbent -> fallback to greedy   <-- previously documented as OPTIMAL
budget 2.0s    +21.3% vs greedy
budget 5.0s    +21.3% vs greedy
```

So the tactical cycle must allow **≥2s of solve time at this scale**, not the
≥1s previously stated here. The earlier figure came from the Python harness on a
sparser instance and does not hold for the shipping code.

This is the failure mode that matters: a cycle tuned to 1s runs greedy on every
tick while the backend table honestly reports `cpsat linked`. You would see no
error, no warning, and no CP-SAT. Re-measure this number per deployment; do not
inherit it. The greedy fallback is load-bearing rather than decorative.

### Superseded: the C++ has now been linked and run

Every number in this section came from a Python implementation that has since
been deleted (two implementations of one model is a bug factory). The C++ path
has now been built against OR-Tools v9.15 and run. See
`bench/results/2026-08-29-ortools-linked.txt` for the full output.

**The formulation is sound.** CP-SAT beats greedy at every scale tested, 0
hard-constraint violations on both sides, strictly more lots assigned at
strictly lower cost:

| lots | tools | pairs | lift over greedy | extra lots |
|---|---|---|---|---|
| 50 | 20 | 307 | +29.9% | +4 |
| 100 | 25 | 714 | +24.2% | +5 |
| 200 | 50 | 1,625 | +25.9% | +8 |
| 400 | 100 | 3,116 | +21.3% | +15 |
| 800 | 200 | 6,626 | +26.6% | +43 |

Two corrections that only linking could have produced:

1. **The +34.4% above is not comparable to the +25.9% here.** Different
   instances: the Python harness reported 859 feasible pairs at 200x60, the C++
   generator produces 1,625 at 200x50. Denser feasibility gives greedy more good
   choices. Neither number is wrong; they were never measuring the same thing.
2. **The 1s budget figure below was wrong.** Corrected in place.
