# Tool dedication overlay — 30 days from the day-90 checkpoint (2026-09-11)

ADR 0013 §3.6. `compare.py --days 120 --warmup-days 90 --starts-scale S
[--overlay OV] --rules fifo,cr,slate`, headless, one process per (fab, scale)
cell, eight concurrent. Every fab resumes from **its own** overlay-keyed day-90
`fifo` checkpoint (§3.5), so absolute lot counts are comparable *down* a cell,
never *across* overlays. The number this page exists to report is the
**slate − cr** delta within each cell.

`idleQ` is the ADR 0013 §3.5 KPI: family tool-hours per day idle while
qualified WIP for that family waited elsewhere in it. `idleF` is the same
without the qualification filter. Coverage is the share of decisions where the
slate held a token for the machine being asked; it is printed for `slate` only.

## The overlays as generated, and why they are milder than §3.2 asked

ADR 0013 §3.2 named `dedication-litho-50`, `dedication-all-50` and
`dedication-all-33`. The generator refused those fractions on SMT2020: the
floors (at least two qualified machines per (family, part)) and the 90%
effective-utilization cap bind hard on a fab where many families have two or
three tools. What it would write instead is 0.70–0.80:

| name | scope | fraction | pairs | hash |
|---|---|---:|---:|---|
| `dedication-litho-70` | litho + implant | 0.70 | 120 | `68d49d48b17ee829` |
| `dedication-all-70` | all families | 0.70 | 780 | `4efd4edf0e5059a3` |
| `dedication-all-80` | all families | 0.80 | 780 | `1b444c70a51fce5f` |

Read the rows below with that in mind: this is a *mildly* dedicated fab, and
the headline result is mostly a statement about that mildness.

## Gates

Both passed before any row was run, and neither is optional (`bench/README.md`).

- **Pristine unchanged.** 2 days from day 0, `slate-cr` reproduced `cr`'s
  **47,149** decisions exactly, fp `8d77d45c4c2654a3` — the fingerprint
  recorded before the `instance.eligible()` refactor. The predicate changed
  nothing on the pristine fab.
- **Gate under each overlay.** `slate-cr` reproduced `cr` exactly on all three:
  45,882 decisions (litho-70), 45,281 (all-80), 45,138 (all-70). The counts
  differing from pristine — and ordering monotonically in how much capacity
  each matrix removes — is the evidence the overlay actually binds rather than
  loading as a no-op.

## Rows

| fab | x | rule | lots/30d | CT d | util % | on-time % | tardiness | idleQ t·h/d | idleF t·h/d | coverage | WIP first→last |
|---|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| pristine | 1.00 | fifo | 1713 | 35.90 | 80.4 | 98.0 | 33 | 337.2 | 411.4 | - | 2053 → 2066 |
| pristine | 1.00 | cr | 1599 | 36.43 | 80.3 | 99.8 | 2 | 316.1 | 387.3 | - | 2054 → 2177 |
| pristine | 1.00 | slate | 1671 | 35.82 | 78.8 | 99.7 | 1 | 357.2 | 469.7 | 45.4% | 2054 → 2108 |
| pristine | 1.03 | fifo | 1705 | 35.70 | 79.6 | 98.0 | 27 | 351.5 | 424.5 | - | 2053 → 2081 |
| pristine | 1.03 | cr | 1600 | 37.50 | 81.0 | 99.9 | 1 | 273.8 | 349.6 | - | 2054 → 2186 |
| pristine | 1.03 | slate | 1753 | 35.78 | 79.9 | 99.4 | 7 | 344.7 | 421.4 | 47.6% | 2054 → 2033 |
| dedication-litho-70 | 1.00 | fifo | 1699 | 35.66 | 80.0 | 98.4 | 24 | 349.5 | 461.2 | - | 2045 → 2069 |
| dedication-litho-70 | 1.00 | cr | 1654 | 37.92 | 80.7 | 99.3 | 4 | 314.1 | 408.7 | - | 2045 → 2116 |
| dedication-litho-70 | 1.00 | slate | 1684 | 35.58 | 80.0 | 99.3 | 9 | 346.5 | 448.7 | 48.7% | 2045 → 2084 |
| dedication-litho-70 | 1.03 | fifo | 1720 | 35.52 | 80.2 | 98.8 | 18 | 337.7 | 420.3 | - | 2045 → 2059 |
| dedication-litho-70 | 1.03 | cr | 1633 | 37.17 | 81.1 | 99.6 | 5 | 285.6 | 359.8 | - | 2045 → 2145 |
| dedication-litho-70 | 1.03 | slate | 1692 | 35.80 | 80.2 | 99.6 | 5 | 329.9 | 435.2 | 48.8% | 2045 → 2086 |
| dedication-all-70 | 1.00 | fifo | 1714 | 35.41 | 79.4 | 98.5 | 20 | 376.3 | 608.3 | - | 2048 → 2056 |
| dedication-all-70 | 1.00 | cr | 1645 | 37.13 | 80.7 | 99.6 | 7 | 319.3 | 497.5 | - | 2048 → 2124 |
| dedication-all-70 | 1.00 | slate | 1685 | 35.71 | 80.0 | 99.4 | 9 | 358.4 | 560.0 | 51.7% | 2048 → 2084 |
| dedication-all-70 | 1.03 | fifo | 1686 | 35.66 | 80.3 | 98.3 | 31 | 335.8 | 547.0 | - | 2048 → 2093 |
| dedication-all-70 | 1.03 | cr | 1665 | 36.90 | 80.5 | 99.7 | 7 | 318.5 | 504.0 | - | 2048 → 2114 |
| dedication-all-70 | 1.03 | slate | 1644 | 35.89 | 79.9 | 99.5 | 9 | 355.6 | 556.0 | 50.7% | 2048 → 2135 |
| dedication-all-80 | 1.00 | fifo | 1731 | 35.47 | 79.9 | 98.7 | 18 | 324.6 | 486.0 | - | 2075 → 2065 |
| dedication-all-80 | 1.00 | cr | 1581 | 37.10 | 80.5 | 99.7 | 6 | 282.4 | 438.2 | - | 2075 → 2214 |
| dedication-all-80 | 1.00 | slate | 1737 | 35.19 | 79.6 | 99.8 | 3 | 335.3 | 523.9 | 48.8% | 2075 → 2058 |
| dedication-all-80 | 1.03 | fifo | 1724 | 35.47 | 80.0 | 98.9 | 13 | 312.6 | 482.1 | - | 2075 → 2083 |
| dedication-all-80 | 1.03 | cr | 1564 | 37.38 | 80.2 | 99.5 | 3 | 293.5 | 443.7 | - | 2075 → 2241 |
| dedication-all-80 | 1.03 | slate | 1764 | 35.73 | 80.0 | 99.4 | 9 | 328.2 | 496.2 | 49.4% | 2075 → 2041 |

### slate − cr, per cell

| fab | x | lots | on-time | cycle time | idleQ |
|---|---:|---:|---:|---:|---:|
| pristine | 1.00 | +72 (+4.50%) | −0.11 pp | −0.62 d | +41.1 |
| pristine | 1.03 | +153 (+9.56%) | −0.45 pp | −1.72 d | +70.9 |
| dedication-litho-70 | 1.00 | +30 (+1.81%) | +0.08 pp | −2.35 d | +32.4 |
| dedication-litho-70 | 1.03 | +59 (+3.61%) | −0.04 pp | −1.37 d | +44.3 |
| dedication-all-70 | 1.00 | +40 (+2.43%) | −0.16 pp | −1.42 d | +39.1 |
| dedication-all-70 | 1.03 | **−21 (−1.26%)** | −0.25 pp | −1.01 d | +37.1 |
| dedication-all-80 | 1.00 | +156 (+9.87%) | +0.15 pp | −1.91 d | +52.9 |
| dedication-all-80 | 1.03 | +200 (+12.79%) | −0.06 pp | −1.65 d | +34.7 |

## Reading

- **The throughput column is not throughput. It is WIP drain, exactly.**
  Both rules receive the same lot releases, so over one window
  `lots(slate) − lots(cr)` must equal `ΔWIP(cr) − ΔWIP(slate)` if neither
  rule actually produced more. It does, identically: residual **0 lots in
  six of eight cells** and ±3 in the other two (rounding on the WIP means,
  which are five-day averages).

  | fab | x | Δlots | WIP drain | residual |
  |---|---:|---:|---:|---:|
  | pristine | 1.00 | +72 | +69 | +3 |
  | pristine | 1.03 | +153 | +153 | **0** |
  | dedication-litho-70 | 1.00 | +30 | +32 | −2 |
  | dedication-litho-70 | 1.03 | +59 | +59 | **0** |
  | dedication-all-70 | 1.00 | +40 | +40 | **0** |
  | dedication-all-70 | 1.03 | −21 | −21 | **0** |
  | dedication-all-80 | 1.00 | +156 | +156 | **0** |
  | dedication-all-80 | 1.03 | +200 | +200 | **0** |

  Every "extra" lot the slate completed came out of work in progress, not
  out of extra production. Over a window long enough for WIP to settle,
  both rules must converge on the start rate. Do not quote these as
  throughput results.

- **There is no dedication signal, in either direction.** An earlier reading
  of this page called the trend "backwards" because the gap is largest on
  `all-80` and negative on `all-70`. That over-read it. The **pristine** fab
  is the control — dedication is absent there by construction — and it shows
  +9.6% at 1.03×, as large as any dedicated fab. When the control moves as
  much as the treatments, the screen has no resolving power, and the
  cell-to-cell spread (−1.3% to +12.8%, one seed) is the noise floor rather
  than a trend to interpret. The correct statement is *no signal*, not
  *reversed signal*.

- **The purpose-built KPI goes the wrong way, in all eight cells.** `idleQ`
  was defined (§3.5) as the quantity a matching solver should reduce and a
  sort key should not. The slate is 32–71 tool-hours/day **worse** than `cr`
  on it everywhere, pristine and dedicated alike. `cr` has the lowest `idleQ`
  of the three rules in every cell. The metric designed to favour the matcher
  reports the matcher leaving more qualified work stranded.

- **Coverage rose instead of falling.** §3.5 expected it to drop under an
  overlay, because more tokens become unusable when tools free in an
  unplanned order. It went 45.4% (pristine) → 48.7–51.7% (dedicated): a
  narrower eligible set means fewer tools compete for each token, so the
  planned tool is more often the one that asks. The prediction was backwards,
  and the mechanism is worth keeping in mind for the reticle overlay.

- **These overlays are too mild to answer the question.** `fifo` sits at
  98.0–98.9% on-time and `cr` at 99.3–99.9% on *every* fab including
  pristine; utilization stays within a point of 80% and cycle time within
  ~2 d, everywhere. A matrix that a myopic rule barely notices is not a test
  of whether a matcher can beat one. This is a consequence of the 0.70–0.80
  fractions the floors forced, not of the mechanism.

- **And the rows were run below the knee, which §3.6 should not have
  specified.** `docs/NEXT.md` §1 established the knee between 1.00 and 1.05
  starts, and that the rules only spread above it — at 1.15× the starts grid
  has `fifo` at 89.0% on-time against `cr`'s 96.1%. At the 1.00×/1.03×
  §3.6 asked for, total tardiness over 30 days is 1–33 lot-days across a
  1,300-tool fab and every rule clears 98% on-time. There is nothing for any
  dispatcher to optimise at this load, dedicated or not, so the experiment
  could not have separated the rules whatever the matrix looked like.
  Dedication needs *queues* to matter: a qualification constraint only costs
  something when a lot has to wait for the specific tool it is qualified on.
  Below the knee there is no wait. `load/` re-runs pristine, `all-70` and
  `all-65` at 1.10× and 1.15× for this reason; read on-time and tardiness
  from those rows, since throughput stays a WIP artifact at any load.

- **The 30-day window cannot carry the throughput numbers, and they should
  not be quoted.** Cycle time is ~36 d, so a 30-day window mostly drains WIP
  that was already in the fab at day 90; small ordering differences push a
  large number of marginal lots across the line. The check: ADR 0012 measured
  the same slate at +1.0% completions over `cr` on pristine at 1.03× over
  **120** days, where these rows show +9.6%. The 120-day confirmation is what
  settles it; treat the deltas above as a screen, not a measurement.

- **What does look real, and is not about dedication:** in 7 of 8 cells the
  slate finishes with *lower* WIP than `cr` (e.g. `all-80` at 1.03×:
  2075 → 2041 against `cr`'s 2075 → 2241) while completing more lots and
  running 1–2 d shorter cycle time, at on-time within half a point. The
  pattern is consistent and appears on the pristine fab too: the slate buys
  `fifo`-like throughput at `cr`-like on-time. That is a restatement of ADR
  0012's operating point, not new evidence for assignment.

## Verdict

**ADR 0012's overturn condition is neither met nor tested by these rows.**
Not met, because nothing here shows the solver earning its keep on a
dedicated fab. Not tested, because the experiment as specified could not
have shown it either way: the only column that moved is a WIP artifact, and
the load is below the knee, where no dispatcher has anything to optimise.
Reporting this as "dedication does not help" would be reading a null
instrument as a null effect.

What the rows *do* establish, and it is not nothing:

- The mechanism works end to end. Both gates pass, the solver receives the
  matrix (863 of 1,313 tools carry a qualified-part list under the
  all-scope overlays), and the pristine fingerprints are untouched.
- On SMT2020 at the 1.03× operating point, **dedication cannot be made much
  harder than this without deleting capacity** — 0.60 is refused, 0.65 is
  the balanced frontier. That is a fact about the testbed that constrains
  every future dedication experiment, and it was not known when §3.2 was
  written.
- The slate runs the fab at lower WIP and 1–2 d shorter cycle time than
  `cr` at equal on-time, on the pristine fab as much as on the dedicated
  ones. A restatement of ADR 0012's operating point, not new evidence.

Order of work from here, cheapest decisive test first:

1. **Load, not matrix** (`load/`, running). Re-run pristine / `all-70` /
   `all-65` at 1.10× and 1.15×, where the starts grid shows the rules
   actually spread, and read on-time and tardiness. Dedication can only bite
   when lots queue for the specific tools they are qualified on; below the
   knee they never wait. If pristine moves as much as the dedicated fabs
   here too, the hypothesis is dead rather than untested.
2. **The harder matrices at the frontier** — `dedication-all-65` (balanced)
   and `dedication-skew-70` (the `--skew` variant §3.2 built and left off),
   both written, at whatever load step 1 shows to be discriminating.
3. **§3.6's 120-day confirmation**, which is what makes a throughput column
   meaningful at all — WIP has to settle before completions measure
   production. Seeds 0 and 1, plus the `slate:none / slate:due / slate:full`
   pressure ablation.
4. **ADR 0013 §7's branch.** Reticles — exclusive across scanners, with a
   transport delay — add a *shared* resource a sort key cannot reason about
   at all, which is a stronger claim than dedication makes.

## Reproducing

```
make -C dispatch slate
baselines/pyscfabsim/.venv/bin/python3 bench/tools/compare.py \
    --days 120 --warmup-days 90 --rules fifo,cr,slate \
    --starts-scale 1.03 --overlay dedication-all-80 \
    --out bench/results/dedication/dedication-all-80_1.03x_30d.json
```

Eight such cells ran concurrently on a 16-core box: `fifo` and `cr` legs cost
~570 s each under that contention (~2.1× their solo cost) and the `slate` leg
~110 min, for ~2h20 wall clock overall.
