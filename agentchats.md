# agentchats — coordination log

Shared working file for the agents on this problem. **Append, don't rewrite
history.** Newest entries at the bottom of the Log. Keep entries short; put
numbers in `bench/results/`, reasoning in `docs/notes/`, and link them.

---

## Goal (from the user, 2026-09-16)

Produce, on SMT2020 LVHM in PySCFabSim:

1. **A realistic rules-based dispatch and fab plan** — the strongest simple
   policy a competent fab engineer would run (dispatch rule + batching +
   queue-time protection), not a strawman.
2. **A grid showing where rules-based breaks down** — axes at least
   queue-time window scale (tightness) and ideally load; metrics good lots
   shipped/day, scrap share, cycle time, WIP stability; all 5 seeds.
3. **A basic but effective optimisation model** — holistic, possibly with the
   critical sections (batch furnaces feeding/closing q-time windows)
   *scheduled* rather than dispatched — that **outperforms the rules in the
   critical ranges**.

"Best results for the fab", reported straight: results are the story, no
reversal narrative.

## Standing constraints (non-negotiable)

- Branch `cqt-window-fix`, worktree `.claude/worktrees/adr-0013-runs`. Commit
  + push to the branch. **Never merge to main, never force-push.**
- Do **not** name the company whose name begins with "V" anywhere in the repo.
- Never expose tunnel tokens, credentials, or `POSTGRES_PASSWORD`.
- Compute: 16 cores / 125 GB. **Total concurrent simulator processes ≤ 16.**
  Claim slots in the Log before launching (`CLAIM n slots: what, ETA`), and
  `RELEASE` when done.
- Code ownership to avoid edit collisions: **lead** owns
  `baselines/pyscfabsim/simulation/**`, `bench/tools/compare.py`,
  `bench/tools/sim_feed.py`, `dispatch/**`. **coordinator** owns new analysis
  scripts it creates, `docs/notes/*` it authors, and the grid plan. Ask in the
  Log before editing the other side's files.
- Every behaviour change: opt-in or keyed into the checkpoint name
  (`sim_feed.ckpt_path`), with a synthetic test in `bench/tests/`.
- Don't read a running log as a result. Paired A/B from the same checkpoint
  before any full-length sweep.

## Roles

- **lead** (the main session): builds rules, the optimisation model and the
  simulator changes; runs paired A/Bs; answers to the user.
- **coordinator** (sub-agent): reads this file, critiques the plan, designs and
  runs the grid and baseline regeneration, reviews the lead's results for
  traps (survivorship, censoring, checkpoint mix-ups, stale tuning), keeps
  the task board current, and writes the lab notes.

## What we know (evidence so far)

- Scrap-on-first is the primary semantics; rework is a sensitivity arm.
  Scrap band (qt, pre-batch-fix): scale 8 ≈ 1–2 %, 5 ≈ 10 %, 3 ≈ 35 %, 2 ≈ 56 %,
  1 ≈ 75 %; fab flow-stable at every scale.
  `bench/results/scrapfirst/SUMMARY.txt`, `docs/notes/2026-09-16-scrap-band.md`.
- Published windows are all feasible (`bench/tools/cqt_window_slack.py`).
- Scrap compounds: only 4–6 % of windows are missed at scale 1, but a lot
  crosses many. Violations concentrate at **Diffusion_FE_94, Diffusion_FE_120,
  Diffusion_BE_123** (batch, min 3–5 lots, same route+step grouping) and
  **WE_FE_108**. `bench/results/cqt_anatomy/`.
- Spiral confirmed: scrap cuts arrivals at FE_94 / BE_123 by >50 %; furnaces
  sit 120–165 tool-h/day free with lots queued but no fireable group.
  `bench/results/batch_fill/`.
- Entrance hold-before-entry (CQT_HOLD_FRAC) **hurts**; estimator is poor and
  34–60 % of violations are not visible at entry. Kept opt-in, not used.
  `bench/results/hold_ab/`.
- Bug fixed `f1b6811`: qt's window tier never reached batch formation (and was
  inverted). Fix = default; `QT_BATCH_TIER=0` reproduces old rows; checkpoint
  suffix `b`. Effect: scale 5 violations −15–20 %, scale 1 ≈ none.
  `bench/results/batch_tier_ab/`. **All published qt rows predate the fix.**
- The current slate (CP-SAT) is a dispatcher: no time dimension, no holds.

## Plan

### Step 1 — feed-the-batch rule (lead)
New rule `qtf` = qt + upstream promotion of lots that complete a waiting
partial batch group (same step+part) within K steps. Synthetic test, then
paired A/B vs `qt` at scales 5 and 1, seeds 0/2.

### Step 2 — critical-section scheduler (lead)
CP-SAT over a rolling 8–12 h horizon for the batch families that close
windows: lot→batch assignment, batch start times, which upstream lots to
expedite; objective lexicographic (blown windows, stalled tool-h, CT). Rest
of the fab stays on the best rule. Paired A/B vs best rule.

### Step 3 — cheap-then-full evaluation + the grid (coordinator designs, both run)
Paired 15-day A/Bs first; full 90-day warm-up + 90-day window only for
winners. Grid: rules {fifo, cr, qt(fixed), qtf, scheduler} × window scale
{8, 5, 3, 2, 1} (× load if budget allows) × seeds 0–4.

### Holistic (after 1–3)
Regenerate baselines under the batch fix; realistic fab plan write-up;
sensitivity arms (rework, under-min firing, hold); paper tables.

## Task board

| id | task | owner | status |
|---|---|---|---|
| T1 | `qtf` feed-the-batch rule + test + paired A/B | lead | built + tested; A/B running (bench/results/qtf_ab) |
| T2 | critique plan; design the grid (axes, cells, compute, ETA) | coordinator | done (Log 2026-09-16 coordinator) |
| T3 | regenerate warm-ups/baselines under batch fix (qp050b) for grid | coordinator | in progress: 20 qt(b) warm-ups running, 8 slots |
| T4 | critical-section CP-SAT scheduler | lead | built + synthetic test (c35ddfe); A/B next |
| T5 | grid runs + analysis script + SUMMARY | coordinator | scripts ready (`sweep_grid.sh`, `analyse_grid.py`); runs wait for T3 + slots |
| T6 | lab note: batch-tier fix + feed-the-batch + grid | coordinator | open |

---

## Log

**2026-09-16 lead** — File created. Plan above. Starting T1. Coordinator:
please take T2 first (read the evidence links, push back on anything weak in
the plan, propose the grid with a compute budget), then T3 when agreed.
No simulator slots claimed yet.

**2026-09-16 lead** — T1 built: rule `qtf` (`dispatcher.FeedTheBatch`) = qt +
promote lots ≤K steps from a batch step whose same step+part group is
waiting below batch_min; tier after qt's window rescue; urgency = earliest
live window deadline in the group, else oldest wait. Refresh 10 min sim.
Test `bench/tests/test_qtf_feed.py` 2/2 (qt fires the furnace after 180 min,
qtf at ~60). All 18 bench tests pass. Checkpoint key `_qp050bf{K}`.
CLAIM 8 slots: `bench/tools/qtf_ab.sh` — qtf K=3,6 × scales 5,1 × seeds 0,2,
15 d from warmed checkpoints; qt arm = `bench/results/batch_tier_ab/*tier1`.
ETA ~25 min. Output `bench/results/qtf_ab/`.

**2026-09-16 coordinator** — T2 done: critique + grid proposal. T3 started.

*Critique of the plan (what would mislead):*
1. **Warm-up rule ≠ measured rule.** A 90-day window on a fab warmed under a
   different rule is ~2.5–4 CTs, but the first ~1 CT (22–37 d) is inherited
   WIP. Every grid rule therefore resumes from ONE shared qt(b) warm-up per
   (seed, scale) — that is what makes rows paired — and the stability verdict
   uses the final-third slope only. Throughput/scrap still include the
   transient; I flag it rather than hide it. Control: one own-rule warm-up
   (fifo, scale 5, seed 0) to size the inheritance before we publish.
2. **qt baselines are stale.** All published qt rows predate f1b6811 and are
   `qp050` (no `b`). No new row may be compared with them. T3 rebuilds.
3. **Survivorship.** On-time (≈99.8 %) and CT are over shipped lots only;
   CT *falls* as scrap empties the fab. Headline = good lots/day + scrap
   share; CT only beside them; on-time dropped from the grid table.
4. **Censoring / conservation.** Lots still in WIP at window end are neither
   shipped nor scrapped. `analyse_grid.py` prints
   cons = (shipped+scrapped+ΔWIP)/starts per cell (1.00 on the old scrapfirst
   rows); anything off 1.0 is a bug, not a result. Warm-up counters are
   pickled into the checkpoint — use compare.py's window-scoped `cqt` fields,
   never `cumulative`.
5. **"Viable" needs a scrap ceiling.** Under scrap-on-first every scale is
   flow-stable, so slope alone never says "breaks". Proposed viable = slope
   < +5/day on every seed AND max scrap share ≤ 15 %; plus "lagging" when
   good/d is >5 % below the best rule in that cell. Report the share threshold
   curve too, so 15 % is not the whole story.
6. **Load via --starts-scale is a step change at day 90**, not a warmed load
   (starts_scale is not in the ckpt key; compare.py applies it on resume). At
   1.1× the fab needs >1 CT to show divergence, so load cells need a 90-day
   window and slope is the only honest verdict. Worth it — it is the axis
   where a dispatcher fails by *jamming* rather than scrapping — but tier B.
7. **Rules in the grid.** fifo and cr are strawmen under q-time; keep them
   as reference rows (cheap) but "rules-based" = best of {qt, qtf}. The
   scheduler (T4) doesn't exist yet: the grid must not wait for it; it slots
   in as extra cells on the same checkpoints. If qtf's K is tuned per scale
   it must be keyed like QT_PROMOTE_FRAC (tuned at scale 5 only = stale
   tuning at 1 and 8, the scale-10 threshold mistake again).
8. **Paired A/B sizes.** 15-day A/Bs with 2 seeds are < 1 CT: good for "does
   it hurt", not for scrap-share deltas of a few points. Gate winners on
   violations/day + scrap share, then confirm on the 60-day grid.
9. Scale 2 adds little between 3 (36 %) and 1 (75 %); scale 8 is the anchor
   where rules work. Tier A uses {8, 5, 3, 1}.

*Grid proposal:*

| tier | rules | scales | load | seeds | window | cells | ETA |
|---|---|---|---|---|---|---|---|
| T3 warm-ups | qt(b) qp050b, h270 | 8,5,3,1 | 1.0 | 0–4 | 90 d | 20 | ~1–2 h @ 8 slots (logs say ~9 min CPU each uncontended) |
| A1 first read | qt, qtf, cr, fifo | 8,5,3,1 | 1.0 | 0,2 | 60 d | 32 | ~1.5–2 h @ 8 slots |
| A2 | same | same | 1.0 | 1,3,4 | 60 d | 48 | ~2–3 h @ 8 slots |
| B load | qt, qtf (+sched) | 5,2 | 0.9,1.1,1.2 | 0,2,4 | 90 d | 36 (+2 warm-ups ×… for scale 2) | ~3 h @ 8 slots |
| C sched | scheduler | 8,5,3,1 | 1.0 | 0–4 | 60 d | 20 | after T4 |

Cells named `<rule>_x<scale>_L<load×100>_s<seed>_w<days>.json` in
`bench/results/grid/`. Metrics: good lots/day, scrap share, CT (shipped),
final-third WIP slope, violations/day, cons check. Aggregation: mean + min–max
over seeds; paired per-seed deltas vs qt for qtf/sched (same checkpoint ⇒
paired). First readable grid (A1) ≈ 3–4 h from now if slots allow.
Settings pinned: QT_PROMOTE_FRAC=0.50, QT_BATCH_TIER=1, scrap-on-first
(`--cqt-max-rework 0`), Demand batching.

*Committed:* `bench/tools/grid_warmups.sh`, `bench/tools/sweep_grid.sh`,
`bench/tools/analyse_grid.py` (bash -n / py_compile clean; analyse tested on
renamed scrapfirst rows, cons = 1.00).

CLAIM 8 slots: T3 qt(b) warm-ups, 20 × (scales 5,1,3,8 × seeds 0,2,1,3,4),
`--days 270 --checkpoint-only`, started 2026-09-16T20:13Z, ETA ~22:00Z.
Output `bench/snapshots/*_cqt{5,,3,8}r0c_qp050b_h270.ckpt`, logs
`bench/results/grid/warmups/`. Will RELEASE when driver.log says end.

*Needs from lead:* (a) confirm the rule name `qtf` as compare.py `--rules`
token and whether K is env-keyed into `qt_tuning_key`; (b) when T1 slots free,
OK for me to take up to 8 more for A1 (16 total cap respected); (c) objection
or not to viable = slope<+5 AND share≤15 %.

**2026-09-16 lead** — Replies to coordinator:
(a) Yes: `--rules qtf` works (plain dispatcher_map name). K is env
`QTF_LOOKAHEAD` (default 3), keyed into `qt_tuning_key` only for qtf
warm-ups — grid cells resume from qt(b) warm-ups so K must go in the cell
filename if varied. Plan: K fixed at 3 unless the A/B says 6 clearly wins;
I'll post which. New token `--rules crit` = CP-SAT critical-section
scheduler (`bench/tools/crit_sched.py`, commit c35ddfe), qtf elsewhere; env
`CRIT_*` knobs; test `bench/tests/test_crit_sched.py` 2/2 (qt blows a window
the scheduler saves by holding the furnace for an arriving member).
(b) Yes — once my qtf A/B releases (5 procs left, ~10 min), take 8 for A1.
I will keep ≤8 for crit A/Bs. Note: crit costs CP-SAT time per plan
(≤2 s × 3 families / 30 min sim) — expect cells ~1.5–3× slower.
(c) Agree: viable = slope < +5/day every seed AND max scrap share ≤ 15 %;
also print the share-threshold curve (5/10/15/25 %) so the cutoff is visible.
Also: please include `crit` in tier C using the same checkpoints; I'll first
validate it on 15-day paired A/B (scales 5, 1, seeds 0/2) from the OLD qp050
checkpoints so it doesn't compete for T3's new ones.

**2026-09-16 lead** — RELEASE 8 slots (qtf A/B done). T1 result
(`bench/results/qtf_ab/`, qt arm = batch_tier_ab tier1, 15 d paired):

| scale/seed | rule | ship/d | scrap/d | share | viol |
|---|---|---|---|---|---|
| 5/0 | qt | 52.2 | 5.7 | 9.9 % | 87 |
| 5/0 | qtf K3 | 48.7 | 4.3 | 8.2 % | 65 |
| 5/0 | **qtf K6** | **52.8** | **4.0** | **7.0 %** | **60** |
| 5/2 | qt | 56.8 | 4.5 | 7.4 % | 68 |
| 5/2 | qtf K3 | 62.1 | 4.6 | 6.9 % | 69 |
| 5/2 | **qtf K6** | **61.9** | **3.5** | **5.3 %** | **62** |
| 1/0 | qt / K3 / K6 | 15.8 / 15.3 / 14.4 | 38.9 / 38.0 / 38.9 | ≈71–73 % | ≈580 |
| 1/2 | qt / K3 / K6 | 12.6 / 12.2 / 12.4 | 43.0 / 43.1 / 43.1 | ≈77–78 % | ≈650 |

Read: at scale 5 feed-the-batch K6 cuts scrap 22–30 % with no throughput
loss; at scale 1 no rule variant moves anything. **Decision: grid uses
`qtf` with `QTF_LOOKAHEAD=6`** (put K in the cell name). Coordinator: 15 d /
2 seeds is below 1 CT, so treat this as "worth gridding", not a result.
~1.8× qt wall time (per-lot lookahead).
T4 smoke (scale 1, 0.3 d): crit runs but holds far too much (29k hold events,
4 planned batches in 7 h) and plans take ~5 s (budget-limited). Debugging the
model/holds before any A/B; no slots claimed.

**2026-09-16 coordinator** — Ack lead replies. `sweep_grid.sh` now exports
`QTF_LOOKAHEAD=6` (cell name `qtfK6_…`) and flags `REBUILT-WARMUP <cell>` if a
cell's log says it built a warm-up. `analyse_grid.py` prints the share-threshold
curve (5/10/15/25 %). T3 warm-ups still running (s0_x5 at day 9/90 after ~8 min
wall at 16-proc load; first batch ETA ~21:00Z, all 20 ~23:30Z — revised from
22:00Z). A1 not launched yet: waiting for the seed 0/2 checkpoints, then a
short end-to-end sanity cell (must resume qp050b_h270, no rebuild, cons≈1.0)
before CLAIM.

**2026-09-16 lead** — T4 debug (`bench/tools/crit_debug.py`): one plan on
warmed scale 1 s0 is OPTIMAL in 0.7–1.2 s per family (47–70 candidates,
5–7 groups); free furnaces get batches at t=0, holds only where members are
inbound. The smoke's "29k holds" are re-offers of parked tools on every lot
free, not real stalls. CLAIM 4 slots: `bench/tools/crit_ab.sh v1` — crit
(QTF_LOOKAHEAD=6 elsewhere) × scales 5,1 × seeds 0,2, 15 d, same qp050
checkpoints as qtf_ab. ETA ~45 min. Output `bench/results/crit_ab/`.
Coordinator: A1 can take ≤4 slots until this releases (8 warm-ups + 4 + 4).

**2026-09-16 21:35Z lead** — RELEASE 4 slots. crit v1 A/B (`bench/results/crit_ab/*_v1.json`):
scrap/day qt / qtfK6 / crit — 5/0: 5.7 / **4.0** / 7.8; 5/2: 4.5 / **3.5** / 4.9;
1/0: 38.9 / 38.9 / 38.5; 1/2: 43.0 / 43.1 / 41.8. **crit v1 loses to qtfK6 at
scale 5** and is ~flat at 1. Causes from its counters: 300–400 of ~2,100 family
solves hit the 2 s budget with no solution at scale 5 (bigger WIP); furnaces
held for inbound members whose ETAs were optimistic (600k+ member-wait
re-offers); 12–20× qt wall time. Building v2: candidates = waiting + ≤1 step
away only; hold only if missing members are due within 45 min, else fire
what is fireable; keep last plan on solver failure; greedy hint; fewer slots.
Warm-ups: all 8 seed 0/2 qp050b_h270 checkpoints exist (18/20 overall).
Coordinator: launch A1 now (sanity cell first), 8 slots. I'll claim ≤4 for v2.
