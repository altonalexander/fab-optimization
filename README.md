# fab-optimization

> **On a public 300 mm fab benchmark (~21,000 lots/year), a calibrated
> assignment solver beat the best dispatching rule we could build on the hard
> seed in all three replicates — on-time delivery 89.6% → 91.2–96.1%, total
> lateness cut 67–85% — at identical output and zero scrap. For a fab shipping
> ~$1.5B of wafers a year, the best replicate is roughly 1,400 more lots
> (~34,000 wafers) delivered on time annually and about $5M less inventory on
> the floor; the worst replicate is about a quarter of that. On an easy seed, where the rule is
> already at 99.65%, the solver loses by 0.6–2.0 points. The cost is 3–7× the
> dispatcher's compute — a rounding error against either number.**
>
> Simulation, three seeds, $3,000/wafer assumed; the full accounting, the
> caveats, and the results that were wrong on the way are in the paper:
> **[When Does an Assignment Solver Beat a Sort Key?](docs/paper/paper.pdf)**
> ([Markdown](docs/paper/paper.md)).
> Watch the fab run live at **<https://fab.frontanalytics.com>**.

| | |
|---|---|
| [![live](docs/screenshots/live.png)](docs/dashboard.md#live--the-fab-right-now) **Live** — the fab right now, KPIs from day 0 | [![live-controls](docs/screenshots/live-controls.png)](docs/dashboard.md#live--playback-control-and-the-assistant) **Playback + assistant** — pause, 1×–1600×, ask the fab questions |
| [![lots](docs/screenshots/lots.png)](docs/dashboard.md#lots--cohort-burndown) **Lots** — a day's releases burning down their routes | [![lot](docs/screenshots/lots-lotview.png)](docs/dashboard.md#lots--one-lot-at-a-time) **One lot** — every step, every wait, every tool |
| [![tools](docs/screenshots/tools.png)](docs/dashboard.md#tools--who-is-busy-who-is-down) **Tools** — who is busy, who is down, by process area | [![tool](docs/screenshots/tool.png)](docs/dashboard.md#tool--one-machines-decisions) **One tool** — its decisions and its queue |
| [![changeovers](docs/screenshots/tool-changeovers.png)](docs/dashboard.md#tool--setups-and-changeovers) **Setups** — changeovers and minimum runs | [![floor](docs/screenshots/floor.png)](docs/dashboard.md#floor--the-cleanroom-as-a-map) **Floor** — the cleanroom as a map, WIP as heat |
| [![products](docs/screenshots/products.png)](docs/dashboard.md#products--the-ten-routes-at-a-glance) **Products** — the ten routes at a glance | [![routes](docs/screenshots/routes.png)](docs/dashboard.md#routes--what-a-products-journey-looks-like) **Routes** — one product's journey, step by step |
| [![slate](docs/screenshots/slate.png)](docs/dashboard.md#slate--the-optimizer-on-demand) **Slate** — the solver's plan, on demand | [![topology](docs/screenshots/topology.png)](docs/dashboard.md#topology--the-pipeline-itself) **Topology** — the four zones and the pipe between them |

Every screen, explained: **[the dashboard, screen by screen →](docs/dashboard.md)**

A wafer fab is the hardest scheduling problem in manufacturing. A silicon lot
makes hundreds of passes through the same few hundred machines, revisiting the
same toolsets at different stages — so the queue you join depends on every
decision made before it. Machines break down, need preventive maintenance,
require setup changes between recipes, and some process wafers in batches that
must be filled. Every time a machine frees up, something has to choose which
waiting lot goes next. That choice is the **dispatching rule**, and it is made
tens of thousands of times a day.

This project replays a full virtual fab from the public SMT2020 testbed so
those rules can be compared on identical demand, identical breakdowns and
identical machine sets — something impossible in a real $10B fab — and builds
a dispatcher to beat the rules the testbed ships with.

Lot dispatching for a 300mm fab, plus the simulator used to judge whether the
dispatching is any good. The two halves live in one repository for exactly one
reason: **they must read the same data.**

```
dispatch/            the C++ stack (fabdisp) — four zones, sub-ms fast path
baselines/pyscfabsim/  discrete-event fab simulator + PPO agent (vendored, read-only)
data/smt2020/        the shared SMT2020 load — the reason this is one repo
                     (LVHM is the standard scenario; see docs/adr/0001)
bench/               comparison harness, per-tool probe, committed results
docs/adr/            why things are the way they are, and what would change them
scripts/             dev-up.sh and friends
```

Start with [`docs/adr/0000`](docs/adr/0000-motivation-scope-and-boundaries.md)
for what this project is for and, more usefully, what it is not.
What comes next, and in what order, is [`docs/NEXT.md`](docs/NEXT.md).

If the dispatcher and the simulator are fed different SMT2020 loads, every
number comparing them is meaningless, and nothing in either program would tell
you. One `data/` directory, symlinked into the baseline, removes that failure
mode. That symlink is load-bearing: if it is ever broken, stop.

## What it solves

Nobody can A/B a dispatching rule in a real fab: the demand, the breakdowns
and the machine set are never the same twice, so a rule that looks better
this quarter may simply have had an easier quarter. Here every candidate
starts from the *same* warmed-up fab — 90 simulated days under `fifo`,
checkpointed — and runs on identical demand and identical breakdowns, so
the only thing that differs between two rows is the rule. That is the page
the whole project exists to fill honestly:

![results](docs/screenshots/results.png)

Every run in the Postgres run store, each resumed from the same day-90
checkpoint: the streaming run beside finished `fifo`, `cr` and `slate`
benchmark rows, with post-switch means and deltas against a chosen baseline,
per-KPI series laid over each other, where cycle time goes (queueing,
batch-holding, processing, delay steps), and the busiest tools per run.
`bench/README.md` says what the numbers do and do not say.

### The KPIs

The numbers on that page are the same KPIs that head every page of the
dashboard, each with an (i) that states its definition; the live tab draws
them from day 0. They are **computed by
the producer**, not the dashboard: `sim_feed.py` samples them once per
simulated hour over a trailing simulated day, during warm-up and live alike,
and publishes them on the compacted `fab.kpi.state` topic keyed by run
(one `KPI_HIST` record with the warm-up series, then one `KPI` record per
hour). The mirror only draws them. That is deliberate: warm-up and live —
and, later, the fifo baseline and the dispatcher — are then measured by one
piece of code, so an A/B compares like with like.

| KPI | definition |
|---|---|
| WIP | lots released and not complete (waiting + on a tool) |
| throughput | lots that completed their route in the trailing day |
| starts | lots released in the trailing day — today the dataset's `order.txt` schedule verbatim; the number a release policy (CONWIP, workload regulation, mix) would be judged on |
| cycle time | mean release→complete of those lots, days |
| on-time delivery | share of those lots done by their due date; mean tardiness of the late ones alongside |
| tool utilization | share of real tools with a lot processing at the sample instant; the `Delay_*` pseudo-toolset (400 stations for fixed waits, ADR 0008) is excluded |
| where cycle time goes | lot-hours in the trailing day spent queueing for a tool, holding for batch partners, processing on a real tool, and sitting in route-prescribed delay steps — as shares |
| busiest tools / toolsets | per run: share of the streamed span each tool had a lot on it, dispatches, queue seen at dispatch; rolled up by family |
| optimized decisions | dispatch decisions in the trailing day that did **not** fall back to the default rule — 0% for the fifo baseline by construction. A dispatcher running inside the simulator stamps `instance.dispatch_source` before `instance.dispatch()`; anything not `rule:*` counts |

`/api/kpi` returns the series; `/api/state` carries the latest sample.

## What we have found so far

The question this project exists to answer is narrow: **does a solver beat a
sort key at the moment a machine frees up?** Not "is scheduling useful" — a
sort key is already a scheduler. The specific claim under test is that solving
an assignment across all waiting lots at once beats ranking them one at a time.

The answer is **yes, narrowly, and only once the objective was calibrated to
the fab's actual numbers** — which took four apparent negatives and one
constant that was wrong by two orders of magnitude.

- **Tool qualification** — not every tool can run every recipe
  ([`0013`](docs/adr/0013-tool-dedication-overlay.md)). The rules did not
  separate. Qualification only *filters* which tools a lot may use, and a sort
  key handles a filter perfectly well.
- **Reticles** — the photomask a lot needs can only be in one machine at a
  time ([`0014`](docs/adr/0014-reticle-overlay.md)). This is a genuine
  *coupling* constraint, the kind a solver should win on. It never bound: on
  this fab there are enough masks, so the whole cost turned out to be the time
  spent moving them. `cr` recovered about a third of that cost. `slate`
  recovered **−0.09%** — nothing — for **14.4× the compute**.
- **Capacity** — the fab has almost no slack
  ([`0015`](docs/adr/0015-right-sizing-the-tool-set.md)). It runs at 72%
  average tool load, and removing just 5% of tools takes on-time delivery from
  98% to under 2%. Capacity here is a cliff, not a dial.
- **Queue times** — some steps must reach the next step within a time limit or
  the work is damaged ([`0016`](docs/adr/0016-queue-time-enforcement.md)).
  This is the one constraint class that makes the fab able to *lose* work
  rather than just be slow, and it is now enforced.

- **Due-date balance** — the last and best-shaped candidate
  ([`0017`](docs/adr/0017-fab-conditions-analysis.md)). With queue times
  enforced, a queue-time-aware sort key (`qt`) leaves the fab stable at full
  load with zero scrap and ~7% of lots late. Measured over 180 days, twice,
  the solver **lost badly** — 48.1 good lots/day against 57.5, on-time 24.9%
  against 81.7%, WIP diverging.

  **That result was wrong, and the reason is the most useful thing here.**
  The solver's queue-time term is `1 + 600/slack_seconds` — written for
  windows measured in *minutes*. This fab's windows are **10 to 240 hours**,
  so a typical at-risk lot received a **1.01×** preference against a due-date
  term reaching 50×. The signal was not weak; it was arithmetically absent.
  Every conclusion about "the solver can't handle queue time" came from a
  solver that had never been told about queue time.

  Made window-relative — one line, no change to the solver — and re-run twice:

  | | good/day | on-time | cycle time | tardiness | WIP |
  |---|---:|---:|---:|---:|---|
  | `qt`, as first written | 57.5 | 81.7% | 38.4 d | 1,925 | stationary |
  | `qt`, with a promotion threshold | 57.4 | 89.6% | 38.3 d | 524 | stationary |
  | `slate`, before the fix | 48.1 | 24.9% | 48.8 d | 91,889 | **diverging** |
  | **`slate`, after** | **57.5** | **93.0%** | **36.9 d** | **118** | stationary |

  Against the *strongest* baseline: **+3.3 on-time points, 4.3× less
  tardiness, 1.4 days shorter cycle time**, level on throughput, violations,
  scrap and stability — at **~5× the wall clock**.

**So the answer is a qualified yes: a minimum viable solver is demonstrated.**
It matches the best sort key on everything that keeps the fab alive and beats
it on lateness. Whether that margin justifies 5× the compute is a business
question, and it now has numbers attached.

Two things we had to withdraw along the way, both recorded in
[`0017 §12`](docs/adr/0017-fab-conditions-analysis.md):

- We claimed the solver won by **rebalancing across a set** — lifting late
  products without hurting early ones — which a ranking supposedly cannot do.
  Giving the sort key a promotion threshold produced the same rebalancing. So
  that was a property of not wasting effort on lots that were never at risk,
  not of solving as a set, and ADR 0009's central claim remains
  **undemonstrated**.
- The published margin is a **lower bound**. Coverage is ~46%, so the `qt`
  fallback decides most of a `slate` run, and the measured `slate` rows
  contained the *untuned* fallback while being compared against the tuned
  rule. Improving the baseline raises the solver's floor too.

The three earlier negatives are untouched by this — different mechanisms — but
their standing **as evidence** is weaker now. If one unchecked constant could
invert a measured, replicated, written-up result, "we tested it and the solver
lost" means less than it reads. Nobody has audited the remaining coefficients
the same way.

The bigger finding is the one we were not looking for: **which simple rule you
choose decides whether the fab is viable at all**, not merely how efficient it
is. A queue-time-aware rule holds WIP stationary at full load with zero scrap;
`cr` and `fifo` diverge on the same fab, same demand, same machines. That is
replicated on five seeds and is worth more than the question it came from.

### What went wrong on the way, and why it is in the ADRs

Every wrong answer this project has produced came from the same place: **a
metric that could not respond to the thing being changed, or a number nobody
checked.** Reading a WIP drain as throughput. Averaging a per-part effect
across the whole fab. Measuring on-time over a window while the fab was
diverging, so the number described where the window was cut rather than the
rule. Comparing a two-day utilisation against a ninety-day one. Optimising
against a queue-time term calibrated in minutes for windows measured in days.

Each fix was a control or an invariant, never a better number. The ADRs record
the mistakes alongside the decisions, because in each case the mistake is the
part that transfers. There is a plain-language account in
[`docs/notes/`](docs/notes/2026-09-12-does-the-solver-earn-its-place.md).

## Architecture

```
   ┌──────────── zone 1: equipment ────────────┐
   │  equipment-sim ──HSMS/SECS-II over TCP──> │
   │                        amhs-adapter       │   no browser reaches here
   └────────────────────────┬──────────────────┘
                            │ ZeroMQ
   ┌────────────────────────▼─── zone 2: realtime ──────────────────────┐
   │                                                                    │
   │   ingest ──> FabState ──> planner ──(CP-SAT, 30-60s)──> Slate      │
   │   (single writer)              │                    (atomic swap)  │
   │                                ▼                                   │
   │   move request ──> slate lookup (~200 ns) ──> decision             │
   └────────────────────────┬───────────────────────────────────────────┘
                            │ Kafka
   ┌────────────────────────▼─── zone 3: data ──────────┐
   │   kafka · postgres · api (read-only)               │
   └────────────────────────┬───────────────────────────┘
                            │ HTTP (nginx)
   ┌────────────────────────▼─── zone 4: enterprise ────┐
   │   ui — React dashboard                             │
   └────────────────────────────────────────────────────┘
```

The zones are enforced by separate Docker networks, not convention. No service
touches both the tools and a browser. `dispatch/infra/zones.yaml` is the
declaration; `dispatch/infra/verify-zones.sh` checks it.

## The components

**The simulator** — `baselines/pyscfabsim/`. A discrete-event model of the fab:
multi-step routes with rework, setup matrices with minimum-run-lengths,
batching, time- and piece-based PM, breakdowns, due dates. Runs 730 simulated
days and reports cycle time, throughput, on-time %, tardiness, utilization.
Pinned upstream at `ae3d55ef`, read-only — changes belong in `dispatch/` or
`bench/`. See its `UPSTREAM.md`. What it simplifies — transport is one
uniform draw for the whole fab, delays are a 400-station pseudo-toolset and
there is no storage — and what that hides from an A/B, is inventoried in
[`docs/adr/0008`](docs/adr/0008-what-pyscfabsim-simplifies.md). Queue-time
constraints were on that list; they are now enforced, with rework and scrap on
violation ([`0016`](docs/adr/0016-queue-time-enforcement.md)). It plays three
roles: fast batch KPI runs for scenario comparison, paced playback for
watching one tool, and the environment the dispatcher itself runs inside.

**The dispatch solver** — `dispatch/include/fab/solver.hpp`. A single-period
assignment: one Boolean per feasible (lot, tool) pair, at-most-one tool per lot,
tool capacity, batch-furnace firing bounds, reticle exclusivity. CP-SAT via
OR-Tools when linked, a cost-ordered greedy otherwise. There is **no time
index** — it assigns, it does not sequence. Backends announce whether they are
actually linked, so an unlinked solver cannot masquerade as a tie with greedy.

**The planner and slate** — `planner.hpp`, `slate.hpp`. The planner solves on a
cycle and publishes an immutable `Slate` by atomic pointer swap. The real-time
path never calls a solver: it reads the slate and answers in ~200 ns, falling
back to an alternate tool if the primary went down mid-cycle. This split is the
core design claim of the system.

**The producer** — `producer_sim.hpp`. A load generator, not a fab model: a
hardcoded product mix, random priorities, coin-flip tool downs. It exists so the
ready pool does not grow unbounded while the pipeline is exercised. Anything
that needs fab physics uses the simulator instead.

**The stores** — two, with different jobs. **Kafka** holds live state: the
compacted `fab.lot.state` / `fab.tool.state` topics are the keyed,
restart-survivable record the dashboard bootstraps from, which is what solves
cold start (see below). **Postgres** (`infra/postgres-init.sql`) is the *run*
store — runs, KPIs and per-tool outcomes, for comparing dispatchers across
seeds and scenarios. It deliberately holds no live fab state; giving the same
fact two homes is how they drift.

**The transport** — `transport.hpp` (Kafka), `zmq_transport.hpp` (ZeroMQ),
`hsms.hpp` and `secs2.hpp` (equipment protocol). SECS-II is real; HSMS is a
state machine with the wire codec stubbed; the Kafka bodies are sketched against
librdkafka and not yet compiled.

**The viewer** — one of them now. `dispatch/ui/` is a React 18 + Vite
dashboard reading the Flask API (`/api/state`, `/api/stream`, `/api/kpi`,
`/api/runs`, `/api/lots`, `/api/zones`). The what-if tab that called
`/api/scenario/compare` — the C++ planner re-assigning a synthetic instance
with tools marked down — is gone from the UI; the endpoint stays for
`scripts/smoke.sh`, which is what exercises the C++ path. `bench/tools/tool_probe.py` used to carry a second
one: a `--follow` mode that drew its own terminal view of a tool with its own
pacing and breakpoints, over a private copy of the run loop. That is gone. The
probe is now headless-only — run the window, report the time budget — and both
it and `sim_feed.py` drive the simulator through `bench/tools/sim_runner.py`,
so a measured run and a published run are the same run rather than two.

What remains of the unification is the transport: the probe still computes the
per-tool time budget in-process instead of publishing `EquipmentState` onto the
stream the API already speaks. Once it does, the React tool view can show the
busy/setup/pm/down/blocked/starved split that today only the terminal has.

## The dashboard

A React dashboard reads the Flask API and mirrors the fab: live floor and tool
views, per-lot burndown, route and product views, the solver's slate on
demand, and a Results tab that compares dispatchers on equal terms.

**[Screen-by-screen tour, with screenshots →](docs/dashboard.md)**

## Cold start

The dashboard is a **mirror**: it holds no state of its own and rebuilds the
fab from the event stream. That is the right design — it means the dashboard
can die, restart, or be opened for the first time mid-shift without anyone
coordinating — but on its own it cannot answer *"what is in the fab right
now?"* at the moment it connects.

It learns a lot exists only when that lot next **moves**. A lot sitting in a
litho queue announces nothing for hours of simulated time, and the ~2,000 lots
the simulator loads from `WIP.txt` are never *released* at all, so they emit
nothing until they happen to dispatch. A freshly started mirror therefore
under-reports WIP for as long as it takes every lot to touch a tool — and it
under-reports it *silently*, which is worse, because a low number looks like a
quiet fab rather than a blind observer.

Replaying history does not fix it. A year is ~15M events, minutes of consumer
time on every restart, and the answer you want is one number per lot, not the
path it took to get there.

**The fix is snapshot + delta.** The producer states the position of every lot
and tool once, as keyed records on a **compacted** Kafka topic, and streams
changes from there. Compaction is what makes this cheap: the log keeps exactly
one record per live key, so a consumer reading from the very beginning reads
*the fab*, not its history. `fab.lot.state` and `fab.tool.state` exist for
this and nothing else.

A discrete-event simulator cannot *start* at day 90 — it has to simulate
there, at roughly 3 minutes of CPU per 30 simulated days (about 35 s for the
default 5 days). So the warm-up is paid **once** and cached in
`bench/snapshots/`, keyed by dataset, seed, dispatcher, batch strategy and
day — and by every constraint overlay in force (tool dedication, reticles,
tool trim, start mix, queue-time settings), because a fab warmed under a
constraint is a different fab from one warmed without it:

- `…_dayN.json` — the dashboard snapshot (positions and per-lot warm-up
  history). `--snapshot-only` republishes it in ~2 s, which populates the
  dashboard but streams nothing.
- `…_dayN_hH.ckpt` — the **whole simulator** at the warm-up line: event
  queue, tool setups, RNG, and the feed's per-lot books. Any later start with
  the same key loads it in well under a second and streams live from day N,
  no re-simulation. `H` is the run horizon (`--days`); a checkpoint can serve
  any run of that length or shorter. `--rebuild` forces a fresh warm-up.

```bash
# first time — simulate 90 days silently, checkpoint, snapshot, stream live
baselines/pyscfabsim/.venv/bin/python3 bench/tools/sim_feed.py \
    --days 180 --warmup-days 90 --speed 20

# every time after — same command; resumes from the checkpoint in <1 s
```

**The warm-up is shared, the rule under test is not.** The checkpoint is
keyed by the rule that ran the warm-up (`--warmup-dispatcher`, default: the
run's own `--dispatcher`), and the rule that takes over at day N is a
separate choice. Warm up once under `fifo`, and `fifo`, `cr` and `slate`
each resume the *same* fab — same WIP, same tool setups, same pending
breakdowns, same RNG — and diverge from there. That is what makes two runs on
the Results tab an A/B rather than two histories; it is also why a `slate`
run does not have to pay hours of its own warm-up. If the warm-up checkpoint
is missing, the feed builds it first (a child `sim_feed.py --checkpoint-only`
under the warm-up rule) and then resumes from it.

```bash
# the dispatcher under test, from the shared fifo day-90 fab
baselines/pyscfabsim/.venv/bin/python3 bench/tools/sim_feed.py \
    --days 180 --warmup-days 90 --dispatcher slate --warmup-dispatcher fifo \
    --speed 1600
```

`slate` cannot sustain 1600x (about 100 s of wall per simulated day, most of
it CP-SAT), so that speed is effectively "unpaced" and the dashboard's speed
menu can still slow it down to watch.

A resumed run continues the same fab state but is not bit-identical to an
uninterrupted one: the simulator iterates a `set` of usable machines and the
set's layout changes across a pickle round-trip, so ties between equivalent
tools can break differently. The trajectory is a valid one, not a replay.

The API applies the snapshot before tailing events, so the dashboard is
populated the moment it starts rather than filling in over the next hour.

## Running it

```bash
scripts/dev-up.sh                     # bring the stack up
baselines/pyscfabsim/.venv/bin/python3 bench/tools/sim_feed.py \
    --dataset SMT2020_LVHM --seed 0 --days 120 --warmup-days 90 \
    --dispatcher fifo                 # warm 90 days, checkpoint, stream live
```

The first run simulates the warm-up and caches it; every run after resumes in
under a second. A head-to-head table, every rule from the same warmed fab:

```bash
bench/tools/compare.py --days 180 --warmup-days 90 --rules fifo,cr,slate
```

**[Starting a session, the two modes, the results tab, the per-tool probe →](docs/running.md)**

## Considerations and enhancements

Things worth doing that are specified but not built. Each has an ADR so
the reasoning survives the backlog.

- **Playback as a cursor** ([0007](docs/adr/0007-playback-is-a-cursor-not-a-throttle.md)).
  Run the simulation and dispatcher unpaced; the mirror advances a
  sim-time watermark at the viewer's speed. The solver's latency is charged
  in fab time, so its budget is exact at any playback speed.
- **Look-ahead dispatch — the hold decision** ([0010](docs/adr/0010-look-ahead-dispatch-the-hold-decision.md)).
  Let a tool wait for a lot it can see coming — out of a delay step, off a
  known process end — when that buys a setup match, a full batch, or a hot
  lot, and only on tools with slack. Needs a "hold until *t*" return from
  the rule, a wake event, and a *held for arrival* bucket in the cycle-time
  split so the cost is visible beside the gain.
- **Downstream-aware dispatch** ([0011](docs/adr/0011-downstream-aware-dispatch.md)).
  The other half of look-ahead: push need *back* up the route, so an
  upstream tool sequences for the litho bottleneck twenty steps on (feed it
  before it starves) or for a furnace batch (send the partners together)
  rather than for its own queue. A pull rule as a baseline first — it is
  what the planner's objective must beat, and it says which term matters.
- **Release control** (no ADR yet). Starts are the dataset's schedule
  verbatim; CONWIP or workload regulation would be a second lever beside
  dispatch, judged on the *starts* KPI. Depends on demand information the
  simulator only sees as `order.txt`.
- ~~**Queue-time constraints** (0008 §2). Parsed, never enforced.~~ **Built**
  ([`0016`](docs/adr/0016-queue-time-enforcement.md)): windows are enforced,
  a violated lot reworks, and a lot that misses too often is scrapped. The
  solver's q-time term is still inert on purpose — it gets un-inerted once
  there is an operating point where violations cost something.
- **Parallel per-family solves** ([0009](docs/adr/0009-slate-rule-hybrid-split.md),
  measured section). A `slate` run spends 60% of its time inside the solver
  and 13% marshalling, so the marshalling everyone remembers as the bottleneck
  is worth only 1.15× now — that fix already landed. The families are provably
  independent but still solved in a serial loop; running them concurrently is
  ~2.1×. Deliberately not built yet: nothing that needs it is running.
- **A self-built simulator** (0008 §6). Three separate cases — speed,
  fidelity where the dispatcher's claims live, one data model — and one
  acceptance test: reproduce PySCFabSim's published baselines on the same
  data first.

## Status

Honest accounting, because the numbers here have been wrong before:

- The build works. `make test` is 56/56; OR-Tools v9.15 is linked and CP-SAT
  runs (`bench/results/2026-08-29-ortools-linked.txt`).
- **The dispatcher is compared to the baseline rules on equal terms**, inside
  PySCFabSim ([`docs/adr/0002`](docs/adr/0002-dispatcher-inside-pyscfabsim.md),
  [`0009`](docs/adr/0009-slate-rule-hybrid-split.md)): every rule resumes the
  same day-90 checkpoint and runs 30 days. LVHM seed 0, days 90→120: `slate`
  completes 1,729 lots at 35.80 d cycle time against `fifo`'s 1,713 / 35.90 d
  and `cr`'s 1,599 / 36.43 d; `cr` keeps the best on-time delivery (99.8% vs
  98.7%). One seed, 47% solver coverage, 9× the wall clock —
  `bench/README.md` has the caveats and `summary.md` the account. The old
  synthetic-instance "+34.4%" number is superseded and should not be quoted.
- **On a fab with real constraints, `slate` does not beat `cr`.** Those rows
  above are the unconstrained fab. With tool dedication and reticles fed in,
  `slate` recovered −0.09% against `cr` at 14.4× the compute
  ([`0014 §7`](docs/adr/0014-reticle-overlay.md)). That meets an overturn
  condition [`0012`](docs/adr/0012-starts-knee-and-what-the-slate-optimises.md)
  set for itself. The open question is no longer "is the slate better here"
  but "is there any operating point where assignment beats ordering"
  ([`0017`](docs/adr/0017-fab-conditions-analysis.md)).
- **Queue-time enforcement is built and the shipped windows bite.** Detection
  costs nothing — a detection-only run is digit-identical to one with
  enforcement off, which is the control that makes the rest trustworthy.
  Violations rework the lot, and a lot that misses too many times is scrapped
  ([`0016 §6`](docs/adr/0016-queue-time-enforcement.md)).
- The tactical cycle needs **≥2s of solve time at 400 lots**, not the ≥1s once
  stated. A 1s-tuned cycle runs greedy on every tick while the backend table
  honestly reports `cpsat linked` — no error, no warning, no CP-SAT. Measured
  and corrected in `dispatch/README.md`; re-measure per deployment rather than
  inheriting the number.
- The producer is `bench/tools/sim_feed.py`, which publishes to Kafka. It
  still has a hidden `--out` that writes JSONL, kept only so `scripts/smoke.sh`
  can run with no Docker and no broker. There was never a C++ `mes_producer`: `Dockerfile.simulator` built it
  from a source file that does not exist and hid the failure with `|| true`.
  That build step and the broken compose service are gone.
- Gurobi and HiGHS are declared backends that fall through to greedy.
- Kafka now runs. `apache/kafka:3.9.0` could not start at all — it died at its
  storage-format step with `advertised.listeners cannot use the nonroutable
  meta-address 0.0.0.0`, reproducible on a bare `docker run` with a fully
  routable `KAFKA_ADVERTISED_LISTENERS`, so it was the image's own env
  handling. Pinned to `confluentinc/cp-kafka:7.7.1`, which accepts the same
  environment unchanged. Broker healthy, all four topics created, and the real
  wire format round trips producer → broker → consumer on `data-net`.
- **Reaching that broker from the host is unverified.** `docker-compose.dev.yml`
  publishes a second listener for host-side producers, but under Docker Desktop
  + WSL2 here the binding never materialises (a plain `docker run -p` does
  publish, so it is compose-specific to this setup). Run the producer inside
  the data zone — the production shape.

See `BUILD.md` for the toolchain and the OR-Tools recipe.

## Credits

This project stands on three pieces of work by other people — a simulator,
a solver and a dataset. If you use it, credit them too.

### PySCFabSim — the baseline simulator

The Python simulator in `baselines/pyscfabsim/` is **not our work**. It is
[PySCFabSim](https://github.com/prosysscience/PySCFabSim-release) by the
Research Group Production Systems, MIT-licensed, vendored here read-only so the
C++ dispatcher has a pinned, reproducible baseline to be measured against.
Every number this project reports as a comparison is a number PySCFabSim
produced.

It is vendored via the fork
[david-dd/PySCFabSim-revised](https://github.com/david-dd/PySCFabSim-revised)
at commit `ae3d55ef`, and **has been modified** — seven deviations, documented
in `baselines/pyscfabsim/UPSTREAM.md`. See `baselines/pyscfabsim/README.md` for
the details and `baselines/pyscfabsim/LICENSE` for the MIT terms, which must be
retained in any redistribution.

### Google OR-Tools — the solver

The tactical layer is a CP-SAT model solved with
[Google OR-Tools](https://github.com/google/or-tools) (Apache-2.0), pinned at
**v9.15.6755**. **We did not build a solver.** The contribution here is the
decomposition and the reformulation — priority ranking rather than scheduling,
tool-group rather than per-tool granularity, a four-hour horizon — and CP-SAT
does the search underneath it. Keeping that line visible is the point of this
section.

> Perron, L. and Furnon, V. *OR-Tools*, Google.
> <https://developers.google.com/optimization/>

If you are describing CP-SAT's *behaviour* rather than just noting the
dependency, cite the solver paper as well — Perron, Didier and Gay on the
CP-SAT-LP solver (CP 2023, LIPIcs). Check the exact bibliographic entry against
the proceedings before you publish it; it is not verified here.

OR-Tools is a large dependency that bundles SCIP, SoPlex, the COIN-OR solvers,
abseil and protobuf, each with its own licence. See
[`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md).

### SMT2020 — the dataset

Both the dispatcher and the baseline read the
[SMT2020 Semiconductor Manufacturing Testbed](https://p2schedgen.fernuni-hagen.de/index.php/downloads/simulation),
distributed by FernUniversität in Hagen under its own terms. It is a separate
work from this project and from PySCFabSim. If you publish results derived from
it, cite:

> Kopp, D., Hassoun, M., Kalir, A., & Mönch, L. (2020). SMT2020 — A
> Semiconductor Manufacturing Testbed. *IEEE Transactions on Semiconductor
> Manufacturing.* doi:10.1109/TSM.2020.3001933

This project standardises on the **LVHM** scenario; HVLM works when passed
explicitly. See `docs/adr/0001-lvhm-default-scenario.md`.

The copy in `data/smt2020/` is redistributed here with attribution. It
carries **no licence of its own** — the distribution states no terms at
all — so read [`data/smt2020/PROVENANCE.md`](data/smt2020/PROVENANCE.md)
before redistributing it further.

## Citing this work

Machine-readable metadata is in [`CITATION.cff`](CITATION.cff) — GitHub renders
it as a **Cite this repository** button, and it lists the dependencies below as
formal references.

```bibtex
@software{alexander_fab_optimization,
  author  = {Alexander, Alton},
  title   = {{fab-optimization}: predictive lot dispatching for a
             300mm semiconductor fab},
  year    = {2026},
  license = {Apache-2.0},
  url     = {https://github.com/altonalexander/fab-optimization}
}
```

If you publish results, cite the three works underneath this one as well.
They are not incidental: PySCFabSim produced every baseline number reported
here, SMT2020 is the load both it and the dispatcher read, and CP-SAT did the
search under every slate decision.

```bibtex
@article{kopp2020smt2020,
  author  = {Kopp, Denny and Hassoun, Michael and Kalir, Adar
             and M\"{o}nch, Lars},
  title   = {{SMT2020} --- A Semiconductor Manufacturing Testbed},
  journal = {IEEE Transactions on Semiconductor Manufacturing},
  year    = {2020},
  doi     = {10.1109/TSM.2020.3001933}
}

@software{perron_ortools,
  author  = {Perron, Laurent and Furnon, Vincent},
  title   = {{OR-Tools}},
  version = {9.15.6755},
  url     = {https://developers.google.com/optimization/}
}

@software{pyscfabsim,
  title  = {{PySCFabSim}},
  note   = {Research Group Production Systems. MIT licence},
  url    = {https://github.com/prosysscience/PySCFabSim-release}
}
```

Quote the `RUN CONFIG` block (below) with any benchmark number, and state the
OR-Tools version — an unversioned CP-SAT result is not reproducible.

There is no DOI for this repository. If you need a citable, archived version,
mint one with [Zenodo](https://zenodo.org/), which snapshots a GitHub release
and issues a DOI; add it to `CITATION.cff` as a `doi:` field afterwards.

## Quoting benchmark numbers

CP-SAT performance changes between OR-Tools releases, so a solver number
without its version is not reproducible. `make bench` therefore prints a
`RUN CONFIG` block before any results:

```
== RUN CONFIG ==
  cpsat version    9.15.6755
  threads          8
  time limit       1 s per solve
  relative gap     0.02
  deterministic    yes
  stopping         first of: proven optimal, gap <= 0.02, or time limit
```

Quote the whole block alongside any table you publish. The version is read from
the linked library via `OrToolsVersionString()` — it is never a compiled-in
literal, so it cannot drift from the binary that produced the numbers.

The same no-silent-fallback rule that governs the backend table governs this:
in a build without CP-SAT linked, the version reads `unavailable (not linked)`
and the run prints an explicit refusal. It is never defaulted to a
plausible-looking value.

## Third-party software

This project links against, bundles, and in one case vendors other people's
work. [`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md) lists every component
with its version, copyright and licence; full texts are in
[`licenses/`](licenses/). Both must travel with any distribution of this
software — source, binaries, or container images.

It is generated from the dependency manifests, not hand-maintained:

```bash
python3 scripts/gen_third_party_notices.py           # regenerate
python3 scripts/gen_third_party_notices.py --check   # CI: fail if stale
```

Adding a dependency without recording its licence is a hard error, by design.

## License

This project is licensed under the **Apache License 2.0** — see `LICENSE`.

You may use, modify, and distribute it, including commercially, provided you
retain the copyright notice, state your changes, and pass along the `NOTICE`
file. It also grants you an explicit patent licence from the contributors.

The Apache-2.0 licence covers this project's own code. It does **not** cover
`baselines/pyscfabsim/` (MIT, see above), the SMT2020 dataset (separate
terms), or any of the libraries it links against. `NOTICE` and
`THIRD_PARTY_NOTICES.md` list all third-party components.
