# SMT2020 spec audit: every column we consume, what it means, where it is read

Written 2026-09-15 after the third meaning-level defect in a week (the
queue-time window instant, the direction of `STEP_CQT`, the units of a
solver constant). None of those was a crash; each was a gap between what a
dataset field means and what the code did with it. This page closes that
gap column by column, so a reviewer can check the reading without trusting
the ADRs. Sources: the LVHM files under `data/smt2020/SMT2020_LVHM/`, the
testbed paper (Kopp, Hassoun, Kalir, Mönch, IEEE TSM 2020) and the
queue-time paper (Kopp, Hassoun, Kalir, Mönch, *Integrating critical queue
time constraints into SMT2020 simulation models*, WSC 2020), and the vendored
loader `baselines/pyscfabsim/simulation/{file_instance,classes,instance}.py`.
The AutoSched AP format documentation is **not** in the repo (the two
`.docx` in `SMT2020.zip` were not vendored); rows marked *unverified* rest
on the code's reading alone.

Values below are counted from the files (`column_values.py` in the session
log; reproducible with `csv` in a minute).

## Findings first

| # | finding | status |
|---|---|---|
| F1 | **Window definition.** WSC 2020 §2.1: "a CQT violation occurs if the time span between the *completion* of the entrance step and the *begin of processing* of the exit step is longer than the prescribed CQT." The code opened the window at the *start* of the entrance step until 2026-09-15. | fixed, ADR 0016 §8; synthetic test |
| F2 | **`STEP_CQT` points forward.** The step carrying it is the entrance; the value names the exit. The simulator reads it that way; the first sizing of F1 had it backwards. | corrected, ADR 0016 §8 |
| F3 | **Exit on the last route step.** Six of ten routes (1, 2, 3, 5, 7, 8) end on a queue-time exit step. Rework and scrap live inside `while remaining_steps`, which never runs for a lot on its last step, so a lot that missed its final window was counted as a violation and then **shipped as a completion**. Every published row undercounts rework/scrap on those six routes' last window (one of ~26 per route). | fixed 2026-09-15; synthetic test; ADR 0016 §9 |
| F4 | **Violation consequence.** The paper says violated lots "have to be scrapped." We rework to the entrance step and scrap on the fourth miss. That is a declared modelling choice, more lenient on material and harsher on capacity than the paper's. `--cqt-max-rework 0` means *unbounded*, so "scrap on first miss" is not expressible today. | recorded; add a scrap-on-first option before the sweep |
| F5 | **No nested windows** in the data (checked: 0 overlapping pairs across 264), 96 chained pairs where one window's exit is the next's entrance. The code handles chaining (close at dispatch, open at completion) and would silently overwrite a nested one; the paper assumes none exist. | verified |
| F6 | **Transport is not zero.** `fromto.txt` carries one row, `Fab → Fab uniform 7.5 2.5 min`; the loader applies it to every step whose family location changes from the previous step's, which is 3,714 of 4,013 route steps (moves into/out of the `Delay` pseudo-location get none). A completed lot accrues **32 h of transport over a 38-day cycle time**. ADR 0008 says this correctly; the paper's §2.2 ("transport time is zero in this configuration") and `docs/NEXT.md` §0.6 did not. What *is* free is the resource: no vehicle, no contention, the tool is released without the move. `--transport-s` (2026-09-15) **overrides** the dataset draw with a constant; 0 keeps the dataset. | paper and NEXT corrected |
| F7 | **Uniform distributions: half-width or full width?** `UniformDistribution(m, l)` samples `m ± l/2`, so `7.5, 2.5` is U(6.25, 8.75) and `PTIME2` halves the spread AutoSched may intend. ADR 0008 wrote U(5, 10). Which is right depends on the AutoSched `uniform` convention, which the repo does not have. Affects every `PDIST uniform` step (all 4,013), every `MTTR uniform`, and transport. Dispatching comparisons are unaffected (same draw for every policy); absolute variability may be understated 2×. | **unverified**; obtain the format doc |
| F8 | **Hot lots** are `PRIOR = 20` (10 of 20 order lines, 38 of 2,154 WIP lots); `HOTLOT` is `no` everywhere and unread. `order_with_superhot_lots.txt` adds one `PRIOR = 30` line and is not loaded. The `PRIOR` value is what the rules and the solver's base priority use. | verified |
| F9 | **The dataset's own dispatch rule** is prescribed per tool (`RULE rule_HotLotFIRST` on 103 families, `rule_LSSU` on 3; `FWLRANK rank_HP;rank_RSETUP;rank_CR`): hot lot first, then priority, then setup match, then critical ratio. That is our `cr` tuple's shape. We replace the rule deliberately; the columns are unread, which is correct, but the paper should say the baseline `cr` *is* the testbed's prescribed rule. | note for the paper |
| F10 | **Columns read nowhere:** `WHEN` (`need` on 401 steps: setup performed when needed — the simulator's only behaviour anyway), `RWKTYPE` (`lot` on all 52 rework steps — whole-lot rework, which is what is modelled), `BATCHCRITF/BATCHPER` (`crit_sameroutestep/piece` on the 10 batch families: batch only same route step, size in pieces — matches `Step.batching` + `BATCHMN/MX ÷ 25`), `PRERULERWL` (`no`), `STNFAMSTEP_ACTLIST`, `IGNORE`, `TRACE`, `ORDER`. None changes behaviour given their values. | verified by value |

## Route files (`route_N.txt`, 4,013 steps over 10 routes)

| column | meaning (spec) | values in LVHM | read at | how used | verified |
|---|---|---|---|---|---|
| `STEP` | step number, 1-based | 242–583 per route | `Step.order` | ordering; `STEP_CQT` target | yes |
| `DESC` | step name / photo layer | e.g. `034_Wet_Etch` | `Step.step_name` | batch key; reticle identity (layer) | yes |
| `STNFAM` | tool family | 105 + `Delay_32` | `Step.family` | eligibility: a step runs only on its family | yes |
| `PDIST`, `PTIME`, `PTIME2`, `PTUNITS` | processing-time distribution, mean, spread, unit | `uniform` on all; `min` | `Step.processing_time` | `get_distribution` → `UniformDistribution(m, l)` = m ± l/2 | **F7** |
| `PTPER` | per lot / per piece / per batch | 2,104 / 1,774 / 135 | `Step` | per_piece × 25 wafers; per_batch → `batching` | yes |
| `PartInterval`, `PartIntUnits` | cascading per-wafer interval | 1,267 steps | `Step.cascading_time` | cascading tools (`STNCAP 2`) | yes |
| `BatchInterval`, `BatchIntUnits` | cascading batch interval | 401 steps | `Step.cascading_time` | as above | yes |
| `BATCHMN`, `BATCHMX` | batch min/max in **pieces** | furnace steps | `Step.batch_min/max` | ÷ pieces per lot (25) | yes |
| `SETUP`, `STIME`, `STUNITS`, `WHEN` | setup id, step-level setup time, when | 182 timed; `need` 401 | `Step.setup_needed/setup_time` | pair times come from `setup.txt`; `WHEN` unread (F10) | yes |
| `RWKSTEP`, `REWORK`, `RWKTYPE` | rework target, %, type | 52 steps, 0.5–1.8 % | `Step.rework_step/rework_percent` | route rework, drawn per lot | yes |
| `StepPercent` | sampling % | 955 steps, 10–100 | `Step.sampling_percent` | `has_to_perform` draw | yes |
| `STEP_CQT`, `CQT`, `CQTUNITS` | **exit step** of the window this step **opens**; length; `hr` | 264 steps, 1–24 h | `Step.cqt_for_step/cqt_time` | ADR 0016; open at completion (F1), close at exit start | yes, F1–F5 |
| `SVESTN`, `FORSTEP` | lens dedication: same tool as at `FORSTEP` | 73 steps | `Step.lot_to_lens_dedication` | enforced in `Instance.eligible` / `dispatch` | yes |
| `IGNORE` | AutoSched comment | — | — | unread | n/a |

## Tool master (`tool.txt.1l`, 106 families)

| column | meaning | values | read | verified |
|---|---|---|---|---|
| `STNFAM`, `STNQTY` | family, count | 913 process + 400 `Delay` | machines expanded per family | yes |
| `STNGRP` | station group | `Litho`, `Dry_Etch`, … | breakdown/PM attach by group; scanner set | yes |
| `STNFAMLOC` | location | `Fab` (105), `Delay` (1) | transport key (F6) | yes |
| `STNCAP` | capacity / cascading flag | `2.0` on 45 | `Machine.cascading` | yes |
| `LTIME/ULTIME` + units | load/unload | 1 min | added to lot and machine time | yes |
| `WAKERESRANK` | `wake_LeastSetupTime` on 9 | | `minimize_setup_time` | yes |
| `RULE`, `FWLRANK`, `BATCHCRITF`, `BATCHPER`, `PRERULERWL`, `STNFAMSTEP_ACTLIST`, `SETUPGRP` | AutoSched dispatch/batch policy | see F9, F10 | unread by design | yes |

## Releases and WIP (`order.txt`, `WIP.txt`)

| column | meaning | values | read | verified |
|---|---|---|---|---|
| `PART`, `PIECES` | product, wafers | 10 parts, 25 | route lookup via `part.txt`; lot size | yes |
| `PRIOR` | priority | 10 / 20 (hot) | `Lot.priority`; hot-lot tier in every rule | F8 |
| `START`, `DUE` | release, due date | `DUE−START` = 16–56 days, two values per part (normal / hot) | `relative_deadline`; due = release + that | yes |
| `RDIST`, `REPEAT`, `RUNITS`, `RPT#` | release interval | constant 258.46 min (normal) / 10,080 min (hot) | release schedule (≈56.6 lots/day) | yes |
| `CURSTEP` (WIP) | starting step | | initial WIP lots placed mid-route | yes |
| `HOTLOT`, `ORDER`, `TRACE` | | `no` everywhere | unread (F8, F10) | yes |

## Setups, breakdowns, PM, transport

| file | meaning | read | verified |
|---|---|---|---|
| `setup.txt` | (from, to) setup time in min, asymmetric, 13 pairs | `Instance.setups` | yes |
| `setupgrp.txt` | minimum run length after a changeover | `setup_min_run` | yes |
| `attach.txt` | which calendar applies to which group/family; first-occurrence distribution | breakdown/PM events per machine | yes |
| `downcal.txt` | MTTF, MTTR (exponential) | `BreakdownEvent` | yes |
| `pmcal.txt` | MTBPM (constant), MTTR (uniform ± F7) | PM events | yes, F7 |
| `fromto.txt` | one row, `Fab→Fab uniform 7.5 2.5 min` | per-step `transport_time` | F6, F7 |

## What the synthetic tests pin (`bench/tests/test_cqt_mechanism.py`)

Nine cases on a three-step route through the real event loop and
dispatcher: window opens at completion (the discriminating case, span 55 min
in a 60 min window passes; the old code failed it), violation above the
window, strict boundary, scale multiplies the window, rework returns to the
entrance and scraps at the cap **on a last-step exit** (F3), detection-only
counts but completes, a skipped sampling step opens nothing, enforcement off
is bit-identical to detection-only, and conservation. All pass.

## Still open

- F4: a scrap-on-first-violation option, so the paper's reading is runnable.
- F7: the AutoSched `uniform` convention. Obtain the format document.
- The same audit for batching, PM and breakdown *timing* (attach `FOA`
  first-occurrence, `move_event` on breakdown) with synthetic tests.
- [done] The offline re-derivation of every reported counter from lot
  histories: see "Counter re-derivation" below. No simulator defect found.

## Counter re-derivation (audit item 3, 2026-09-15)

`bench/tools/rederive.py`. A plugin records the raw history during a run --
one row per step start, one per step completion, one per release and
completion -- and an offline pass rebuilds every reported number from that
history alone. The violation count is rebuilt from the dataset's own window
table and the recorded timestamps, never from `cqt_waiting`, `cqt_deadline`
or the violation flag, so a disagreement between the simulator's bookkeeping
and the data shows up as a count difference.

Four configurations, 20 days, seed 0, all agree EXACTLY:

| run | lots | violations | reworks | scraps |
|---|---|---|---|---|
| fifo, scale 10 | 1,181 | 1 | 1 | 0 |
| fifo, scale 1 | 385 | 6,150 | 4,909 | 1,215 |
| qt, scale 1 | 911 | 2,193 | 2,036 | 134 |
| qt, scale 10, detection-only | 1,056 | 0 | 0 | 0 |

Throughput, cycle time, on-time %, tardiness, conservation
(`seen = done + scrapped + WIP`) and `counter_cqt_scrapped ==
len(scrapped_lots)` match in every run.

**No simulator defect was found.** Every initial disagreement was a defect in
the checker, and each one is worth recording because each is a trap for
anyone else reading this code:

1. **Step order is not unique across routes.** 264 windows span only 208
   distinct orders, and up to 10 steps share one order. A window table keyed
   by order matches entrance steps against other products' exits. Key by step
   identity.
2. **`lot_end_time` at dispatch is a PREDICTION.** `move_event` pushes a
   completion later when the machine breaks down or enters PM. The window
   opens at the true completion; using the prediction read a 0.25 h span as
   10.29 h. Take the completion from the next `on_lot_free`.
3. **The route has its own rework.** 52 steps carry `REWORK`; it truncates
   `processed_steps` exactly as a queue-time rollback does. Separate them by
   the landing step (a queue-time rollback lands on the window's entrance,
   which carries `STEP_CQT`): 82 rollbacks on the fifo/scale-10 run, of which
   1 was queue-time.
4. **A chained window's entrance can be voided.** 96 windows are chained --
   one window's exit is the next one's entrance -- so a violation AT that step
   rolls the lot back past it. The simulator correctly opens no window from a
   rolled-back step; a checker that does over-counts by 442 on the fifo
   scale-1 run.
5. **Inferring a rollback from the next dispatch censors the horizon.** 184
   lots on the qt/scale-1 run were rolled back and never started another step,
   so the rollback was invisible. Read the processed-step count at the free
   event instead.

One check was reframed rather than fixed: cycle time is **not** equal to
`waiting + processing + transport`. `processing_time` is process + load +
unload only, so the span also absorbs setup and any breakdown or PM that lands
while the lot is on the tool -- a mean 6-14 h per lot. The one-sided invariant
is what holds and is what is asserted: the accumulators never exceed elapsed
time.

Also noted, not a defect but a reporting hazard: on a run with **no warm-up**
the reported cycle time is dominated by initial WIP, whose `release_at` is
backdated before t=0 (1,177 of the 1,181 completions on the fifo/scale-10
run). The published protocol warms up first, which is what makes the number
mean anything.
