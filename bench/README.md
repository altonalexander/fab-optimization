# bench — dispatcher vs. baseline

The comparison only means something if both sides read `data/smt2020/`. That is
the invariant this directory exists to protect.

## Not yet built

The harness is empty on purpose. It cannot be written honestly until the C++
CP-SAT path is linked against OR-Tools and shown to reproduce the numbers in
`dispatch/README.md` (+34.4% cost reduction over greedy, 200 lots x 60 tools).
Until then there is nothing to compare that is known to be correct.

Order of work:

1. Link OR-Tools, run `make bench`, check it against the recorded numbers below.
2. If it matches, commit the C++ output to `results/` as the new source of truth.
3. If it does not match, the formulation has a bug and the recorded numbers are
   wrong. Fix that before writing any harness.

## Recorded numbers, and their provenance

Everything below came from a Python implementation (`tools/cpsat_bench.py`) that
was **deliberately deleted** — keeping two implementations of one model is the
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

## Baseline side

`baselines/pyscfabsim/` reports throughput/cycle-time over SMT2020 HVLM and LVHM
under FIFO and CR, plus a PPO agent. Its metrics are not yet mapped onto the
dispatcher's objective; doing that mapping is step 4, and it is a real modelling
question, not glue code.
