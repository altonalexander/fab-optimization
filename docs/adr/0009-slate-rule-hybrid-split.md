# 0009 — `slate_rule`: where the line between Python and C++ falls

**Status:** Accepted, being built. Supersedes the integration half of
[0002](0002-dispatcher-inside-pyscfabsim.md); the experiment 0002 describes is
unchanged, this records how it is wired.

## The question

0002 says "plug the dispatcher into PySCFabSim" and prefers pybind11 over IPC
because "the decision-point loop is too hot for IPC." Before writing the
binding, three things were measured. All three changed the design.

## What the numbers say

**LVHM, from `data/smt2020/SMT2020_LVHM`:**

| | |
|---|---|
| machines | 1,313 |
| station families | 106 (≈12 machines/family) |
| steps per route | ~400, over 10 routes |
| initial WIP | 2,154 lots |
| lots completed, 730 days | ~39,600 |

So a 730-day run is **~16 million dispatch decision points** and WIP/family is
roughly **25 lots against 12 machines**.

**From `./fabtest --bench 2`, OR-Tools 9.15 linked:**

```
   lots  tools   pairs     solver  assigned   objective     solve
    800    200    6626     greedy       239    34263635     1.0ms
    800    200    6626      cpsat       282    25151072   2031.1ms
```

CP-SAT beats greedy on objective at every size, with zero violations. But the
solve column reads 2031ms, 2014ms, 2010ms, 1899ms — **pinned at the time limit
at every problem size**. Those are best-incumbent-in-2s, not optimal.

### Consequence 1: the hot path is the rebuild, not the lookup

At N=60 simulated seconds, 730 days is ~1.05M slate rebuilds; the sweep 0002
wants (N=30) is ~2.1M. At 2s/solve that is arithmetically impossible.

The 16M decision points, by contrast, are dict lookups against a snapshot. If
the slate crosses into Python once per rebuild, **none of the 16M lookups cross
a language boundary at all.**

0002's stated reason for preferring pybind11 is therefore the wrong one. The
conclusion survives, the reasoning does not.

### Consequence 2: per-family decomposition is a precondition, not an optimization

The eligibility matrix is already block-diagonal. A lot at step *s* is eligible
only for machines in `step.family`; `evaluate()` rejects everything else. There
are no cross-family constraints in the model, so solving per-family is **exact**,
not an approximation — the one coupling, reticle exclusivity, is litho-internal.

Per family that is ~25 lots × ~12 machines ≈ 300 variables: two orders of
magnitude below the benchmark rows above, and millisecond-class. The 106
families are independent and parallelizable.

Combined with lazy invalidation — re-solve only families whose state moved since
the last rebuild — this is what makes the run tractable.

### Consequence 3: the models don't line up, and that is the real cost

`fabdisp::Lot` was:

```cpp
lot_id, product_id, recipe, reticle, wafer_count, priority, qtime_slack_s
```

What SMT2020 and PySCFabSim have that this **cannot express**:

- **Setup groups and an asymmetric setup matrix.** C++ had
  `setup_s = (lot.recipe == current_recipe_) ? 0 : changeover_s_` — one flat
  changeover. SMT2020 has `SETUPGRP` and a `setup.txt` matrix of pair-dependent,
  asymmetric times.
- **Minimum run length.** `grep min_run dispatch/` returned nothing. The sim's
  `machine.min_runs_left`/`min_runs_setup` gate is slot 0 of every ptuple and
  `greedy.py:120` calls it *"extrem wichtig."*
- **Due dates.** `Lot` had no deadline field at all — yet the comparison is
  against CR, a due-date rule, on tardiness and on-time-% KPIs.
- Lot dedications; batch families keyed on `step_name + part_name`, not recipe.

Marshalling the sim's richer state through the old `Lot` would have dropped
exactly the information that decides dispatch quality. **The binding was never
the expensive part.**

## Decision

### 1. The simulator stays Python, untouched

Not because Python is the right language for a discrete-event fab model — from
scratch, at 16M events, it would not be — but because the *validated model* is
the asset and the code is incidental. Rewriting it is 0002's explicit anti-goal,
and it would put `slate` back on a different harness from `fifo`/`cr`/PPO, which
is the exact problem 0002 exists to remove.

`baselines/pyscfabsim/` remains vendored read-only. Everything new lives in
`bench/` and `dispatch/`.

### 2. The split follows the line fabdisp already drew

`Lot::priority` is documented as *"from the tactical urgency vector"* — fabdisp
already assumes urgency is computed upstream and handed in as a scalar. That
settles where the line goes:

```
Python (bench/)                          C++ (dispatch/)
──────────────────────────────           ──────────────────────────────
owns route, due dates, remaining         owns tool eligibility model
work, downstream congestion,             and the CP-SAT assignment
batch-fill pressure
                                         machine_config.hpp  (tool model)
collapses them into                      solver.hpp          (CP-SAT)
  priority, qtime_slack_s                planner.hpp         (per-family)
        │                                        │
        └──────────► C ABI, once per rebuild ◄───┘
                     ~2,500 lots in, ~2,500 tokens out

        16M decision points never cross: they read the returned snapshot
```

This is a hybrid by design, not by compromise. The C++ keeps the defensible
core — the tool model and the solver — and gets the three model gaps closed,
which is work worth doing on its own merits.

### 3. The channel is a C ABI + ctypes, not pybind11

0002 preferred pybind11. This environment has neither pybind11 nor Python
development headers, and `ctypes` needs neither: `libfabslate.so` is built by
the same `g++ -std=c++20` line as everything else and loaded from the standard
library.

The ergonomic advantages of pybind11 are real but they are priced for a *fine*
boundary. This boundary is coarse — one call per rebuild, flat POD arrays in and
out — so they buy little. Revisit if the surface grows.

In-process (either mechanism) is preferred over the ZeroMQ path
(`zmq_transport.hpp`) for one reason beyond speed: **determinism**. A seeded
730-day run must reproduce. An IPC boundary with wall-clock timeouts quietly
does not. For the same reason the benchmark must use CP-SAT's deterministic
time limit, not its wall-clock one — `SolveParams::deterministic` exists for
this and must stay true.

### 4. `slate_rule` is a sort key, and its tuple shape is load-bearing

The integration point is `greedy.py:71`:

```python
lot.ptuple = ptuple_fcn(lot, time, machine, setups)
wl = sorted(machine.waiting_lots, key=lambda k: k.ptuple)
```

Two constraints follow, and both are easy to violate silently:

- The batching path at `greedy.py:83-89` indexes `ptuple[0]` (the min-run gate)
  and splices `ptuple[2:]` (the priority rule). A differently-shaped tuple
  changes batch formation, and the A/B then measures two things at once. Slots 0
  and 1 are copied verbatim from the upstream rules; the slate contributes at 2+.
- The sim asks *"which lot for this machine"*, never *"which tool for this lot"*
  — and `find_alternative_machine` (`greedy.py:37`) may reassign the machine
  afterwards. So the slate needs a **tool → ranked lot list** inverse index; the
  lot→tool assignment is only ever honored implicitly.

A lot arriving between rebuilds has no token. Those must not fall through to
FIFO or a large fraction of decisions would not be the slate's, and the
benchmark would measure a blend. The fallback is a **solver-consistent score** —
the linearized form of `SolverExporter::cost` — so behavior is continuous across
the coverage boundary.

## Consequences

**Accepted.** The C++ gains a `FamilyTool` configuration modelling SMT2020
semantics (setup groups, asymmetric setup matrix, minimum run length, batch
families on step+part). `Lot` gains `family`, `setup_group`, `due_s`,
`batch_min`, `batch_max` — additively, so the existing tool classes and their
tests are unaffected.

**Accepted.** Two artifacts must agree that did not have to before: the C++
cost model and the Python pressure layer. Pinned by a shared test vector — same
lots, same tools, assert identical assignment — so drift is caught rather than
discovered.

**Accepted.** v0 ships a pure-Python rule that reproduces `cr` *exactly* before
any solver is involved. If it does not reproduce, the harness is wrong and every
downstream number is too. This is the highest-value step and it is mostly
plumbing.

**Not settled.** HSMS/SECS-II, the four network zones and the ~200ns fast path
still have no representation in a discrete-event sim, and stay on their existing
evidence track. Two tracks remains correct.

**Open risk, stated before the run.** The solver has no time index and cannot
sequence. Against CR over 730 days with setup minimum-run-lengths, a per-cycle
assignment blind to ordering may lose on cycle time while winning on per-cycle
objective. Minimum run length is now *modelled*, which narrows this, but does
not remove it. That remains an informative result, not a bug.

## What would change this

- A measured rebuild cost that stays intolerable after per-family decomposition
  and lazy invalidation. The fix then is a larger N or a coarser trigger, not a
  different language — the solve is inside OR-Tools' C++ either way.
- The C ABI surface growing past flat POD arrays. Then pybind11 earns its
  dependency and 0002's original preference stands.
- `slate` losing to `cr` on cycle time *because* of missing sequencing. That
  argues for a time-indexed model, which is a different ADR.
