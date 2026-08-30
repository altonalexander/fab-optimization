# bench — dispatcher vs. baseline

The comparison only means something if both sides read `data/smt2020/`. That is
the invariant this directory exists to protect.

## Status

Step 1 is done. OR-Tools v9.15 is linked, the C++ CP-SAT path has been run for
the first time, and the formulation is sound: it beats greedy at every scale
with 0 violations. Full output and the two corrections it forced are in
`results/2026-08-29-ortools-linked.txt`.

Remaining:

- Reconcile the instance generators. The C++ generator and the deleted Python
  one produce different feasibility densities (1,625 vs 859 pairs at ~200 lots),
  which is why the lift figures differ. Until they agree, cross-harness numbers
  cannot be quoted together.
- Map PySCFabSim's metrics (throughput, cycle time, tardiness under FIFO/CR/PPO)
  onto the dispatcher's assignment objective. This is a real modelling question,
  not glue code: the baseline optimises a schedule over time, the dispatcher
  optimises an assignment at an instant. Deciding what a fair comparison even
  means is the work.
- Only then is there a harness worth writing.

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
and the tests that would overturn it are in [`SCENARIO.md`](SCENARIO.md). Read
it before quoting any number as being about "the fab" — it is about one of two
fabs, and section 5 records evidence that already pushes back on the rationale.

## Baseline side

`baselines/pyscfabsim/` reports throughput/cycle-time over SMT2020 HVLM and LVHM
under FIFO and CR, plus a PPO agent. Its metrics are not yet mapped onto the
dispatcher's objective; doing that mapping is step 4, and it is a real modelling
question, not glue code.
