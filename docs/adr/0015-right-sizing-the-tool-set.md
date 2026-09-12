# 0015 — Right-sizing the tool set, and the demand model that was lying

**Status:** Implemented 2026-09-12. Prerequisite for ADR 0016 (queue-time
enforcement) rather than an experiment in its own right: a constraint can only
cost the fab something if blocking **propagates**, and it cannot propagate
through slack.

---

## 1. Why this exists

ADR 0013 and ADR 0014 both measured nothing, and both had the same underlying
reason once the diagnosis was done: **a blocked lot always found another
tool.** `DE_FE_86` has 118 tools in one family; block a lot there and it has
117 alternatives. Qualification could not bite, reticle exclusivity could not
bite — the constraint mechanism was sound in each case and the fab simply
absorbed it.

So before spending another round on a new constraint class, size the fab so
that absorbing is no longer free.

## 2. The demand model was wrong, and it is worth recording how

The first pass at this concluded the fab ran at **55% designed load** and was
massively over-tooled, and recommended removing a third of the tools. That was
wrong, from two compounding mistakes.

**An unweighted family mean.** Averaging load across 105 families weights a
1-tool metrology station the same as a 118-tool etch family. The unweighted
mean is 39%; the tool-weighted figure is what matters.

**A silent zero in `machine_seconds_per_lot`.** The demand model read
`step.cascading_time.m` behind a `hasattr` guard, falling back to `0.0`. But
`baselines/pyscfabsim/simulation/tools.py` has three distribution classes with
three different attributes:

| class | attribute | `avg()` |
|---|---|---|
| `UniformDistribution` | `.m` | yes |
| `ConstantDistribution` | `.c` | yes |
| `ExponentialDistribution` | `.p` | **no** |

So every step with a constant or exponential process time contributed **zero
demand** — 45 of 105 families, which read as "never dispatched to". Fab-wide
load came out at 53% instead of **72.3%**. Note also that a naive switch to
`.avg()` is not the fix: it raises `AttributeError` on the exponential class.
`gen_overlay.dist_mean()` now handles all three.

**What this did and did not corrupt.** It did not touch ADR 0014's verdict:
the litho families use `UniformDistribution`, so scanner and mask demand were
always computed correctly — re-measured mask load is 33.7% against 34.8%
before, and the reticle table hash is byte-identical. It did understate
fab-wide load everywhere, and it made ADR 0013's capacity check *permissive*
for the 45 affected families (its refusals, which were driven by litho, were
sound; anything it waved through elsewhere was not checked).

This is the fourth time in this line of work that a wrong answer came from a
metric that could not respond to what was being changed. The others are in
[the lab notes](../notes/2026-09-12-does-the-solver-earn-its-place.md).

## 3. The decision

### 3.1 A trim is data beside the dataset, applied to the tool master

`data/smt2020/overlays/<name>/trim.tsv`: one row per family, `STNFAM` and the
new `STNQTY`. Families absent keep their count.

Applied in `sim_runner.build`, **between `read_all` and `FileInstance`** —
that is the whole design. Rewriting `STNQTY` before construction means the
simulator builds exactly the fab we asked for: machine indices are dense and
contiguous, `family_machines` is right, and every utilisation denominator
counts the right tools. Deleting machines from a built instance would require
fixing up `free_machines`, `usable_machines`, `family_machines` and every
`machine.idx` that `lot.dedications` keys on — and the failure mode of getting
that subtly wrong is a plausible number, which this project has had enough of.

Never an edit to `data/smt2020/SMT2020_*`. ADR 0001 makes LVHM the default
scenario and ADR 0013 §2 refused to touch the testbed so the pristine fab
stays the comparable baseline; a trim is the same kind of object as a
qualification matrix or a reticle library, and keys its own checkpoint
(`trim.key()`) because **a trimmed fab is a different fab** and must not
resume a checkpoint warmed on the full tool set.

### 3.2 Sizing

`bench/tools/gen_trim.py --target U`: for each family, the tool count that
puts its static load at or below `U` at the reference start rate.

- **It only ever removes slack.** A family already above the target is left
  alone, so the fab's true constraint keeps its capacity and the knee is not
  manufactured by starving the bottleneck.
- **Floors.** Never below `--min-tools` (default 2); batch families keep at
  least 2 so one breakdown does not serialise the mix through a single
  furnace; `Delay` is never touched — 400 pseudo-stations for fixed waits
  (ADR 0008), not capacity.
- **Rework is not modelled**, so real demand is a few percent above this,
  which makes the trim conservative — the right direction for a floor.

Written so far, from a 72.3% baseline over 913 process tools:

| trim | target | tools | removed | fab-wide static load |
|---|---:|---:|---:|---:|
| — | — | 913 | — | 72.3% |
| `trim-82` | 82% | 865 | 48 (5%) | 76.3% |
| `trim-88` | 88% | 817 | 96 (11%) | 80.8% |

### 3.3 Per-family utilisation on every row

`compare.py` reports `family_util` — mean busy tools, tool count and
utilisation per family over the reporting window, accumulated rather than
sampled into the row series. Two reasons: 60 families × 2,160 hourly samples
would dominate the file, and the question "did this tool set land where we
sized it for" only needs the window mean.

More importantly, a fab-wide average is exactly what hid three effects
already. It reports the fab at 80% while one family is at 88% and another at
5%. Sizing decisions need the distribution, not the mean.

## 4. What this assumes

- **Static load predicts run utilisation.** It should, to within the variance
  breakdowns and PM add, and §3.3 exists to check rather than assume it.
- **The published tool counts are a sizing choice, not physics.** SMT2020's
  counts come with the benchmark; trimming them is a deliberate deviation.
- **A trimmed fab is not comparable to external SMT2020 results.** Accepted:
  the comparison that matters for "does the solver earn its place" is rule
  against rule on one fab. It is why a trim is a named variant and not a new
  default.

## 5. What would overturn it

The pre-registered prediction, recorded before the runs:

1. **The knee should barely move.** It is set by the busiest family
   (`LithoMet_FE_19`, 88.1%), which the trim by construction does not touch.
2. **The cliff should steepen.** At baseline, 1.05× starts saturates one
   family while the rest sit near 77%; under `trim-88` it should push a couple
   of dozen families toward saturation together, leaving far less slack to
   absorb a blocked lot. That, not the knee, is the point.
3. **Run utilisation should track the static sizing**, and per-family
   utilisation should land in the bands §3.2 predicts.

If run utilisation does *not* follow the static figure, the demand model is
still wrong somewhere and every capacity check in ADR 0013, 0014 and here is
suspect. That is the result that would send this back to the start.

If the cliff does not steepen — if a trimmed fab absorbs a blocked lot as
easily as the full one — then slack was not the reason ADR 0013 and 0014
measured nothing, and the explanation lies somewhere this has not looked.
