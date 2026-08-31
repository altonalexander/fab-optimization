# 0008 — What PySCFabSim simplifies, and what that hides

**Status:** Accepted as a record, 2026-08-30. This is an inventory, not a
decision to replace the simulator; the decision it informs — whether and when
to build our own — is left open in §6 and belongs with 0002.

---

## 1. Why write this down

Every KPI on the dashboard and every row on the results tab is a number
PySCFabSim produced. The A/B the project is building toward — the dispatcher
against the rules SMT2020 ships with — will be judged on those numbers. So it
matters, precisely, what the simulator does *not* model, because a dispatcher
can only be rewarded for things the simulator notices. A rule that is better
because it makes fewer transport moves cannot win here, since moves are free.

Everything below is read from the vendored code at
`baselines/pyscfabsim/simulation/` (pinned at `ae3d55ef`) and the LVHM data in
`data/smt2020/SMT2020_LVHM/`, with file references so it can be re-checked
when either changes.

## 2. Transport, delay and storage — the parts that are almost not there

### Transport is one number for the whole fab

`fromto.txt` has a single row:

```
FROMLOC  TOLOC  DDIST    DTIME  DTIME2  DUNITS
Fab      Fab    uniform  7.5    2.5     min
```

Every tool family sits at location `Fab` (`STNFAMLOC` in `tool.txt.1l`), so
every step-to-step move is one draw from U(5, 10) minutes
(`file_instance.py:28-45`). There is no distance, no direction, no bay.

How it is applied is more telling than the number. In `instance.get_times()`
(`instance.py:212-220`) the transport draw is added to the **lot's** completion
time — the lot becomes available for its next step `tt` later — but not to the
**machine's**: the tool is freed at `cascade + load + unload`. So transport is
a per-lot delay with no resource behind it: no vehicles, no rails, no load
ports, no contention, and no way for two lots to want the same move at once.
At 22,000 dispatches a simulated day the fab makes 22,000 moves a day and none
of them ever wait.

### Delay steps are a pseudo-toolset

Route steps such as `101_Delay` are served by the tool family `Delay_32`,
which `tool.txt.1l` declares with `STNQTY 400` — four hundred "machines" of
zero load and unload time. A delay is therefore modelled as a tool with
effectively unlimited capacity: the lot occupies one of 400 stations for the
step's process time and nothing queues. That is fine for what it stands for
(cure, cool-down, wait-for-inspection) and it is why `Delay_32_*` appears in
the dashboard's decision stream as if it were a tool — the dispatcher counts
those as decisions, which the optimized-decision KPI should discount.

### There is no storage

A lot between steps is not anywhere. On `LotDoneEvent` it is appended to the
`waiting_lots` list of **every** machine in its next step's family
(`dm_lot_for_machine.py:free_up_lots`), and removed from all of them when one
dispatches it. Queues are unbounded lists with no capacity, no location and no
cost; there is no stocker, no FOUP, no shelf, no transfer between storage and
tool. "Waiting" in the KPIs is time on those lists — pure dispatch delay with
no physical component.

### Queue-time constraints are read and ignored

The routes carry queue-time limits (`STEP_CQT`, `CQT`, `CQTUNITS` in
`route_*.txt`; parsed into `Step.cqt_for_step` / `cqt_time`,
`classes.py:127-128`). The handling in `Instance.dispatch()` is commented out
with a `TODO` (`instance.py:156-165`) and `counter_cqt_violated` is never
incremented. So a lot can sit past its CQT indefinitely with no scrap, no
rework and no penalty — a dispatcher that respects time-critical queues gets
no credit, and one that ignores them pays nothing.

## 3. What it does model, and how

To be fair to it — this is a real fab model, and considerably more than a
job shop:

| aspect | how | where |
|---|---|---|
| routes | 10 products, 200–580 steps, revisiting families | `route_*.txt` |
| process time | per-step distribution, scaled per piece; lots of 25 wafers, never split | `classes.py:96-115` |
| batching | min/max batch per step, same product + same step; four batching strategies | `classes.py:120-121`, `greedy.py` |
| cascading tools | `STNCAP == 2` pipelines lots at a per-lot interval rather than the full process time | `classes.py:36`, `instance.py:220-225` |
| setups | setup matrix per family, min-run-length after a setup, route-planned setups | `setup.txt`, `setupgrp.txt`, `instance.py:226-240` |
| breakdowns | exponential MTTF/MTTR per family; setup cleared on breakdown | `downcal.txt`, `events.py:56-108` |
| preventive maintenance | time-based and piece-based, per calendar | `pmcal.txt`, `instance.py:169-174` |
| sampling | `StepPercent` skips metrology steps at random | `classes.py:122,138-140` |
| rework | probabilistic, splices completed steps back onto the route | `RWKSTEP`/`REWORK` |
| priorities and hot lots | priority 10 vs 20, separate weekly hot-lot stream, `HotLotFIRST` rules | `order.txt`, `tool.txt.1l` |
| dedications | a step can be pinned to one machine of a family | `Lot.dedications` |
| dispatch trigger | when a machine frees up **and** has waiting lots; the rule picks lots for that machine | `dm_lot_for_machine.py`, `greedy.py` |

Two properties of that last row shape everything the dispatcher can do:

- Decisions are **machine-centric and reactive**. The question is always
  "this tool just freed up; which of the lots already waiting for it goes
  next?" There is no look-ahead to lots that will arrive in five minutes, no
  reservation of a tool for a lot in transit, and no decision at all while a
  tool is busy. A slate-based dispatcher (0006) fits this shape — it answers
  the same question faster — but a planner that wants to *hold* a tool for an
  imminent batch has no hook to do so.
- Downtime **shifts** rather than interrupts. `BreakdownEvent.handle` samples
  the outage and pushes the machine's pending events out by it
  (`instance.move_event`, `instance.py:84-92`). A lot mid-process is delayed,
  never scrapped or re-queued; a breakdown during a batch delays the batch.

## 4. What the real thing has that this does not

Listed against the categories above, roughly in order of how much they move
the numbers a dispatcher is judged on:

- **AMHS.** Overhead-hoist vehicles on rails with finite count and speed,
  stockers with finite ports and capacity, inter-bay vs intra-bay moves,
  traffic and deadlock, FOUP handling time at each load port, and load ports
  themselves as a resource (a tool with two ports and three lots arriving
  is a queue the simulator cannot see). Transport time in a real fab is a
  distribution with a long tail that depends on where the fab is congested
  *right now*, and dispatchers are routinely judged on move count.
- **Queue-time constraints** as hard rules with scrap or rework on violation;
  entire dispatch policies exist only to protect them.
- **Cluster tools.** A "machine" is often several chambers with their own
  states, mixed recipes, internal robots and wafer-level (not lot-level)
  timing; process time is a function of what else is loaded.
- **Reticles and other secondary resources** for litho — a lot cannot start
  without the mask, which is on another tool or in a stocker; operators and
  shifts; consumables.
- **Metrology feedback.** Sampling here is a coin flip that skips a step; in a
  fab the measurement result *holds* lots, triggers rework, adjusts the next
  tool's recipe (run-to-run control) and changes the sampling rate.
- **Lot dynamics.** Splits and merges, engineering holds, priority changes
  mid-route, lot-size variation, wafer-level scrap.
- **Dispatch latency and the MES.** A decision is not instantaneous: the MES
  round-trip, the vehicle dispatch, the load-port handshake. 0007 is about
  charging the *solver's* latency; the rest of that path has no model here.
- **Tool qualification and matching.** Which machines of a family may run
  which recipe changes over time; dedications here are static.

## 5. What this means for the numbers

- **Relative comparisons between dispatch rules are fair.** fifo, cr and the
  dispatcher all see the same simplifications; a rule that wins here wins on
  queue-and-batch discipline, which is real. This is why 0002 insists the
  dispatcher be run *inside* this simulator rather than on its own generator.
- **Absolute numbers are not fab numbers.** Cycle time excludes transport
  contention, storage handling and CQT scrap; utilization is of the tool, not
  of tool-plus-ports; on-time delivery is against due dates with no
  hold-driven slippage. Treat them as a ranking scale, not a forecast.
- **Some advantages cannot be shown at all.** Fewer moves, protected
  time-critical queues, port-aware batching, reticle-aware litho sequencing —
  a dispatcher built for these will look no better than fifo here. If the
  dispatcher's pitch depends on them, this simulator cannot make the case.
- **Some advantages are overstated.** With free transport and infinite queues,
  aggressive batching and tight setup-avoidance are cheaper than in a fab
  where every held lot occupies a stocker slot and every move competes for a
  vehicle.

## 6. Whether to build our own

The case for a self-built (C++) simulator is now concrete rather than
aesthetic, and it is three separate cases:

1. **Speed.** Single-threaded Python tops out near 10,000x (0007, measured).
   A dispatcher that needs a thousand seeds × ten scenarios × a year each is
   CPU-bound on the simulator, not the solver.
2. **Fidelity where the dispatcher's claims live.** An AMHS with ports and
   vehicles, hard CQTs, and a dispatch hook that runs *before* a tool frees
   up. None of these are patches to PySCFabSim; each is a different event
   model.
3. **One data model.** 0002 records that the dispatcher's instance generator
   and the simulator disagree (1,625 vs 859 feasible pairs at ~200 lots) and
   that the KPI mapping is an open modelling question. A simulator that
   shares `fab/` headers with the planner closes that gap by construction.

Against it: PySCFabSim is *published*, and every number it produces can be
checked against the SMT2020 paper and PySCFabSim's own reported baselines.
A new simulator has no such anchor until it reproduces those baselines on the
same data — which is therefore the acceptance test for any replacement, and
the reason `data/smt2020/` is symlinked rather than copied (README, top).

**Decision deferred** to a follow-up to 0002, on the evidence that ADR calls
for: run the dispatcher inside PySCFabSim first. If it wins on the things
this simulator does model, build the C++ simulator to find out whether it
still wins on the things it does not. If it does not win here, fidelity is
not the problem.

## 7. How to know whether this record is right

- Re-check §2 against the vendored source whenever `UPSTREAM.md` changes;
  the pin is what makes these line references meaningful.
- The optimized-decision KPI should **exclude** `Delay_*` families once the
  dispatcher is in the loop, or a fifth of its "decisions" are delay steps
  with nothing to decide.
- If a future run reports CQT violations, §2's claim about the commented-out
  handling is stale and this record needs updating — that is the cheapest
  fidelity gain available and worth taking if upstream lands it.
