# bench — dispatcher vs. baseline

The comparison only means something if both sides read `data/smt2020/`. That is
the invariant this directory exists to protect.

## Status

**The harness exists.** `tools/compare.py` runs any set of rules — including
the CP-SAT slate — on one dataset, one seed and one horizon, and prints the
table. The design decisions behind it are in
[`docs/adr/0009`](../docs/adr/0009-slate-rule-hybrid-split.md).

```
python3 bench/tools/compare.py --days 30 --rules fifo,cr,slate
python3 bench/tools/compare.py --days 2  --rules cr,slate-cr   # validate first
```

It needs `dispatch/libfabslate.so`, built by `dispatch/build-slate.sh`
(or `make -C dispatch slate`), and the baseline's venv for pandas.

### The validation gate

`slate-cr` routes through the slate's call path — same signature, same tuple
shape, same rebuild hook — but returns CR's ordering. It **must** reproduce
`cr` exactly, and `compare.py` checks this by fingerprinting the whole dispatch
sequence rather than comparing KPIs (on a short horizon no lot has finished and
every rule scores 0, which would let a broken harness "validate").

Measured, 2 days, LVHM seed 0: **47,149 decisions, identical, fp
`8d77d45c4c2654a3`.** If this ever fails, nothing else in the table means
anything.

### What was still open, and what closed it

- ~~Reconcile the instance generators.~~ Moot. There is now ONE generator:
  every rule runs inside PySCFabSim, so the 1,625-vs-859 feasibility-density
  mismatch cannot arise.
- ~~Map PySCFabSim's metrics onto the dispatcher's objective.~~ Done by
  construction, not by translation. The dispatcher inherits the simulator's
  routes, horizon and KPIs; `compare.py` computes cycle time, throughput,
  on-time % and tardiness from `instance.done_lots` for every row identically.

### Still open

- **730-day runs are expensive.** Measured on LVHM: ~1,440 planning cycles per
  simulated day at N=60s, ~5 ms of CP-SAT each across the dirty families. A
  full 730-day slate run is hours. 30-day runs are minutes and are what the
  numbers below come from. Parallelising the per-family solves is the obvious
  next lever and is not done.
- **Coverage is ~57%, not ~100%.** That is the share of decision points where
  the slate had a pick for the machine being asked; the rest fall back to a
  solver-consistent score. It is reported in every row, because a low-coverage
  row measures the fallback more than it measures the slate.
- The pressure ablation (`slate:none` / `slate:due` / `slate:full`) is wired
  but has not been run as a full ladder.

### Two measurements worth keeping

**A 5 ms per-family budget beats a 50 ms one.** Three simulated hours, LVHM:

| budget | plan time | coverage |
|---|---|---|
| 50 ms | 20.5 s | 56.2% |
| 20 ms | 12.3 s | 58.6% |
| 5 ms  |  6.3 s | 58.8% |

3.3x faster and *slightly better*. A per-family model is ~25 lots against ~12
machines; CP-SAT closes it well inside 5 ms and the rest of the budget goes on
proving optimality nobody collects. This is the decomposition thesis of
adr/0009 confirmed by measurement.

**Planning only for currently-free tools does not work.** `usable_machines` is
the handful awaiting a decision at that instant, so a slate built from it holds
a few tokens and ~94% of decisions fall through. Planning across the whole fab
and serving lookups as tools free — which is what the production dispatcher
does — took coverage from 6.3% to 56.6%.

## Recorded numbers, and their provenance

Everything below came from a Python implementation (`tools/cpsat_bench.py`) that
was **deliberately deleted**, and is SUPERSEDED by
`results/2026-08-29-ortools-linked.txt`. Kept for provenance. Everything below — keeping two implementations of one model is the
dual-implementation problem the port to `src/test_main.cpp` removed. So these
are the claim to be reproduced, not evidence.

```
200 lots x 60 tools, 859 feasible pairs
            assigned    objective     solve   status
greedy            69      6,984,848      0.4ms  GREEDY
cpsat             80      4,584,282   5005.9ms  OPTIMAL
cost reduction vs greedy: +34.4%   lots assigned: +11   violations: both 0
```

Scaling sweep, 5s budget:

| lots | tools | pairs | greedy | cpsat | lift |
|---|---|---|---|---|---|
| 50 | 20 | 205 | 0ms | 2.9s | +44.8% |
| 200 | 50 | 842 | 0ms | 5.0s | +23.2% |
| 800 | 200 | 3,367 | 2ms | 5.0s | +28.5% |

The operational constraint: at 400 lots a 0.25s budget returns no incumbent at
all and the greedy fallback fires. The tactical cycle must allow >=1s of solve
time at this scale. The fallback is load-bearing, not decorative.

## Which scenario

**LVHM, by default, everywhere.** The reasoning, the assumptions it rests on,
and the tests that would overturn it are in [`docs/adr/0001`](../docs/adr/0001-lvhm-default-scenario.md). Read
it before quoting any number as being about "the fab" — it is about one of two
fabs, and section 5 records evidence that already pushes back on the rationale.

## Baseline side

`baselines/pyscfabsim/` reports throughput/cycle-time over SMT2020 HVLM and LVHM
under FIFO and CR, plus a PPO agent. Its metrics are not yet mapped onto the
dispatcher's objective; doing that mapping is step 4, and it is a real modelling
question, not glue code.
