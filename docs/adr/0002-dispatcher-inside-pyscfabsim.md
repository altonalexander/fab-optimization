# 0002 — Run the dispatcher inside PySCFabSim to compare it

**Status:** Proposed, not built. This is the experiment that would settle
claim 2 in [0000](0000-motivation-scope-and-boundaries.md), and it has not been
run.

**Original note:** proposal, not built. Nothing in this file is implemented. It records
the intended shape of the comparison harness that `README.md` says does not yet
exist.

## Why

`dispatch/`'s own `producer_sim` / `equipment_sim` are load generators, not fab
models: no routes, no due dates, no rework, breakdowns are a coin flip, and a
default run is ~96 lots over seconds of wall clock. They exist so the ready pool
does not grow unbounded. Growing them into a fab model means rebuilding
PySCFabSim, in C++, without validation.

PySCFabSim already is the environment. Plug the dispatcher into it.

## Today — two programs that share only a dataset

```
  PySCFabSim (baselines/pyscfabsim)          fabdisp (dispatch/)
  ─────────────────────────────────          ───────────────────
  discrete-event, 730 sim-days               10s planning cycle
  routes, setups, batching, PM,              single-period assignment
  rework, due dates, breakdowns              (no time index)
        │                                          │
        │ rule = sort key                          │ producer_sim  ── synthetic
        │ (fifo | cr | lifo | PPO)                 │ equipment_sim ── ~96 lots
        ▼                                          ▼
  cycle time, throughput,                    assignment objective,
  tardiness, on-time %, util                 lots assigned, ns latency
        │                                          │
        └────────────┐                ┌────────────┘
                     ▼                ▼
              data/smt2020/  ← the only shared thing
              (baselines/pyscfabsim/datasets is a symlink to it)

  Not comparable: different generators (1,625 vs 859 feasible pairs at
  ~200 lots), different horizons, different KPIs. The +34.4% headline
  measures 21-30% across the two harnesses.
```

## Proposed — one environment, four rules

```
                        bench/  (integration lives HERE)
                        baselines/pyscfabsim is vendored read-only
   ┌───────────────────────────────────────────────────────────────────┐
   │                                                                   │
   │   PySCFabSim instance  ── 730 sim-days, warm-up reset at 1 yr     │
   │                                                                   │
   │   greedy.py:262                                                   │
   │   machine, lots = get_lots_to_dispatch_by_machine(inst, RULE)     │
   │                                        │                          │
   │                    ┌───────────────────┴──────────────────┐       │
   │                    ▼                                      ▼       │
   │            fifo / cr / lifo                        slate_rule     │
   │            (sort key, upstream)                    (proposed)     │
   │            PPO (gym env)                                 │        │
   │                                                          │        │
   └──────────────────────────────────────────────────────────┼────────┘
                                                              │
              every N SIMULATED seconds        lookup, every decision point
                    (N ~ 30-60s, the                          │
                     production cycle)                        │
                          │                                   │
                          ▼                                   ▼
   ┌───────────────────────────────────────────────────────────────────┐
   │  fabdisp, via pybind11                                            │
   │                                                                   │
   │   Planner::build_slate(ready lots, tool status)                   │
   │        │  CP-SAT: x[lot,tool] over feasible pairs                 │
   │        │  at-most-one tool/lot; tool capacity;                    │
   │        │  batch furnace [min,max] when fired;                     │
   │        │  reticle exclusivity (AddAtMostOne)                      │
   │        ▼                                                          │
   │   Slate  ── immutable, atomic swap                                │
   │        │   lot_id -> {primary, alternate, rank, expected_s}       │
   │        │   ToolId -> DispatchSlice                                │
   │        ▼                                                          │
   │   Slate::lookup(machine) ── rank machine.waiting_lots             │
   │                             no solve on this path                 │
   └───────────────────────────────────────────────────────────────────┘

   Result: one table, one generator, one horizon, one KPI set.

     rule    | cycle time | throughput | on-time % | tardiness
     --------+------------+------------+-----------+----------
     fifo    |            |            |           |
     cr      |            |            |           |
     PPO     |            |            |           |
     slate   |            |            |           |
```

## Why the slate is rebuilt on a cycle, not per decision point

PySCFabSim decides per event, one machine at a time. Over 730 days that is a
very large number of decision points; a CP-SAT solve at each one never
finishes. Rebuilding every N *simulated* seconds and serving lookups in between
is not a benchmark compromise — it is exactly the production timing, and it
turns stale-slate degradation into a measured quantity. Sweeping N gives a
curve that does not currently exist.

## Constraints

1. `baselines/pyscfabsim/` is vendored read-only (see its `UPSTREAM.md`). The
   rule module and runner live in `bench/`. No edits inside the vendored tree
   are needed, because `dispatcher` is a passed-in parameter.
2. No Python binding exists today. Options: ZeroMQ to a `fabdisp` subprocess
   (`zmq_transport.hpp` exists, truer to the deployment) or pybind11 around
   `Planner::build_slate` + `Slate::lookup`. Prefer pybind11 — the decision-point
   loop is too hot for IPC.

## What this does and does not settle

Settles: the KPI-mapping question and the instance-generator mismatch, both by
construction — the dispatcher inherits PySCFabSim's routes, horizon and metrics,
and there is then only one generator.

Does not settle: HSMS/SECS-II, the four network zones, and the ~200ns fast path
have no representation in a discrete-event sim. Those stay on their existing
evidence track (`make hsms-test`, the latency histogram in `e2e_main.cpp`). Two
tracks is correct; one harness should not be made to prove both.

Expected risk, worth stating before the run rather than discovering it: the
solver model has no time index and cannot sequence. Against CR over 730 days
with setup minimum-run-lengths, a per-cycle assignment blind to ordering may
lose on cycle time while winning on per-cycle objective. That is an informative
result, not necessarily a bug.
