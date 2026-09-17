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
| T3 | regenerate warm-ups/baselines under batch fix (qp050b) for grid | coordinator | done: 30 qt(b) warm-ups, scales 8/5/4/3/2/1 × 5 seeds |
| T4 | critical-section CP-SAT scheduler | lead | built + synthetic test (c35ddfe); A/B next |
| T5 | grid runs + analysis script + SUMMARY | coordinator | in progress: lanes A–E running; partial `bench/results/grid/GRID_SUMMARY.txt` |
| T6 | lab note: batch-tier fix + feed-the-batch + grid | coordinator | draft `docs/notes/2026-09-17-where-rules-break.md` (update when crit arm + lanes land) |

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

**2026-09-16 lead** — Correction to the v1 diagnosis: the scale-5 plan
failures were **MODEL_INVALID**, not timeouts — a furnace busy past the
horizon got an empty start domain, so FE_94 (and others) went unplanned and
fell back to qtf mid-plan. Fixed (tools busy past horizon are skipped). v2
also: lookahead 1 step, horizon 8 h, 2 slots/tool, ETA = queue pad + run for
queued lots / half-run for in-process, hold only when the batch start or a
missing member is ≤45 min away, last plan kept on solver failure. Scale-5
plans now FEASIBLE/OPTIMAL at ≤2 s/family. Tests 2/2.
CLAIM 4 slots: `bench/tools/crit_ab.sh v2`, ETA ~60 min, `bench/results/crit_ab/*_v2.json`.

**2026-09-16 21:36Z coordinator** — Sanity cell passed: qtf K6, scale 5,
seed 0, 3-day window → log says `resumed …seed0_qt_Demand_day90_cqt5r0c_qp050b_h270.ckpt`,
no "building it", cons 1.04 (3-day window; noise), 55 s wall.
Warm-ups: 18/20 done, all rc=0; s4_x3, s4_x8 still running (2 slots).
CLAIM 8 slots: A1 = qt, qtfK6, cr, fifo × scales 5,1,3,8 × seeds 0,2, 60 d,
32 cells, started 21:33:50Z, ETA ~23:30Z. Output `bench/results/grid/*_w60.json`,
driver `bench/results/grid/A1_driver.log`.
CLAIM 4 slots (deferred): A2 = seeds 1,3,4 (48 cells) at 4 jobs, auto-starts
via `bench/tools/grid_chain_a2.sh` when the warm-up driver ends (which also
releases those 2 slots); refuses to start if any warm-up failed. Coordinator
total ≤ 12. A2 ETA ~03:00Z at 4 jobs; I'll raise it to 8 when A1 releases.
RELEASE of warm-up slots will be logged when the driver ends.

**2026-09-16 22:25Z lead** — RELEASE 4 (crit v2 done). crit v2 scrap/day vs
qtfK6: 5/0 5.9 vs **4.0**; 5/2 4.5 vs **3.5**; 1/0 **36.7** vs 38.9; 1/2 42.3 vs
43.1. Shipped/day 5/0 48.1 vs 52.8. **Still loses at scale 5**; small gain at
scale 1 on one seed only (noise-level). Holds still dominate its counters
(~950k member-wait re-offers). Ablation next: v2 with holds off
(CRIT_HOLD_MAX_S=0) to separate "batch composition planning" from "idling
furnaces". CLAIM 4 slots, ETA ~50 min, `bench/results/crit_ab/*_v2nohold.json`.
Working hypothesis for the grid story: furnace-local scheduling has little
headroom over qtfK6, because the violations at tight scales are born upstream
(transit > window in 36–57 % at scale 1, cqt_anatomy). The next model should
schedule the *window zone* (entrance step → intervening → exit furnace)
jointly — release lots into a window only when the plan has exit capacity —
i.e. an optimised version of the hold that failed as a heuristic.
Coordinator: please sanity-review this hypothesis against A1 when it lands.

**2026-09-16 22:25Z lead** — RELEASE 4: the no-hold ablation was killed at launch by the harness low-memory guard (free RAM ~106 GB available, but 15 sim processes already running: A1 + A2 + warm-ups). Deferred until A1 releases (~23:30Z). No lead slots in use.

**2026-09-16 22:50Z lead** — A1 partial read (24/32): at scale 5 qt 54.9 good/d 7.5% share vs qtfK6 53.7 / 8.4% over 60 d (the 15-d qtf edge does not hold); fifo/cr 27–32%; scale 1 every rule 74–75%. Several qt-family cells flagged BREAKS on slope +6–10 — coordinator please check whether that is post-warm-up transient in a 60-d window before we trust the verdict. CLAIM 4 slots (detached nohup): batch-min bound — qt from qp050 checkpoints with every batch_min=1: scale 3 seed 0 base + minb1, scales 5 and 1 seed 0 minb1 (bases exist). 15 d, ETA ~15 min, `bench/results/bound_ab/`.

**2026-09-16 23:25Z coordinator** — RELEASE 8 slots (A1 done 23:09Z, 32/32 rc=0;
warm-ups also done 21:48Z, 20/20 rc=0 — those 2 slots released then).
A2 raised to 8: original driver (4 jobs, seed-major 1→3→4) plus
`sweep_grid.sh 4 … "4"` (`A2b_driver.log`) on seed 4 only. CLAIM stays 8 total
for A2, ETA ~01:30Z. Coordinator total 8 (+ lead 4 = 12).

A1 read (`bench/results/grid/A1_SUMMARY.txt`, n=2, seeds 0/2, 60 d). good lots/day · scrap share:

| scale | qt | qtfK6 | fifo | cr | best |
|---|---|---|---|---|---|
| 8 | 56.5 · 2.3 % | **56.8 · 1.0 %** | 52.5 · 15.2 % | 44.9 · 30.6 % | qtfK6 ≈ qt |
| 5 | **54.7 · 7.9 %** | 53.9 · 8.7 % | 46.7 · 26.5 % | 42.7 · 31.9 % | qt (qtfK6 −1.4 %) |
| 3 | 37.2 · 33.9 % | **37.4 · 34.6 %** | 35.6 · 38.2 % | 32.6 · 44.1 % | qtfK6 ≈ qt |
| 1 | 13.8 · 75.7 % | 14.5 · 74.0 % | **14.5 · 74.0 %** | 14.0 · 75.3 % | all within 5 % |

**Slope verdict review:** the +6..+10 BREAKS on qt/qtfK6 at 8/5/3 are not
climbs. Last-third mean WIP is within ±3 % of the first third and end WIP ≤
day-90 WIP in every flagged cell. Causes: a post-warm-up dip then rebound, and
10-day WIP swings of ±100–150 lots that make a 20-day OLS tail ±10/day noise
(`bench/tools/grid_wip_trace.py`). **Proposal (not applied):** stability =
first-third→last-third mean-WIP drift per day < +5 on every seed; final-third
slope stays as a diagnostic; confirm with 90-day windows for scales 8/5 × qt,
qtfK6 × seeds 0/2 (8 cells). Under it: viable at 8 and 5 = qt, qtfK6 only;
nothing viable at 3 or 1 (scrap). Lead: agree/object?

**On the 22:25Z hypothesis:** A1 supports it, with a caveat.
(1) qtfK6 buys nothing over qt at 60 d (scale 5 slightly worse; the 15-d
edge was noise), and crit v1/v2 lost. Two furnace-side levers, no gain.
(2) At scale 1, fifo = qtfK6 = cr = qt within 5 %: ordering is irrelevant
there, so the loss is capacity/timing, not sequencing at the exit.
(3) Caveat: "transit > window in 36–57 %" can't mean processing+transport
(cqt_window_slack: no window infeasible, tightest 1.46× min), so it must
include queueing at the entrance tool or intervening steps. That is
consistent with "born upstream" but also with "exit-tool queue at release
time", so check which queue the time is spent in before building the zone model.
The batch-min=1 bound is the right discriminator: if it doesn't move scale-3/1
scrap, the furnace isn't the constraint. (4) The zone model means admission
control, which idles entrance capacity. Judge it on good lots/day, not scrap
share; the hold heuristic hurt on exactly that.

**2026-09-16 23:30Z lead** — RELEASE 4 (bound_ab done). **Key finding** (`bench/results/bound_ab/`, qt, seed 0, 15 d): with every batch_min = 1, scrap share at scale 3 falls **35.2% → 2.0%** (shipped 41.3 → 46.7/d); scale 1 71% → 39% (15.8 → 25.2/d); scale 5 9.9% → 9.1% but shipped 52.2 → 49.5/d (underfilled runs cost capacity). So tight-window scrap is overwhelmingly lots waiting for batch partners under a HARD min — my "born upstream" hypothesis (22:25Z) is withdrawn. Lever = when to fire an underfilled batch, a real capacity-vs-scrap trade, i.e. where optimisation should win.
Built rule `qtfw` (commit 8d0f90c): qtf + fire an underfilled same-step+part group when a member has < QTFW_SLACK_H (2) h of window left (optional QTFW_MAXWAIT_H timeout); WakeEvent books the tool re-offer at the threshold. Test `test_qtfw_fire.py` 3/3; all 23 bench tests pass. Key `_qp050bf6w2`.
Deviation note for the user: firing below the dataset batch_min is a declared deviation (min-batch-with-exception, as fabs run it). Main-result framing to confirm with the user; run as a parallel arm now.
CLAIM 4 slots (detached): `bench/tools/qtfw_ab.sh` — qtfw at scales 5/3/1 + qtfK6 at 3, seed 0, 15 d. ETA ~20 min. `bench/results/qtfw_ab/`.
Coordinator: add `qtfw` to A2/grid once this A/B confirms; and please revisit the grid axis — scale 3 is now the critical range.

**2026-09-16 23:50Z coordinator** — (1) **Viable definition changed, as agreed with lead:**
`analyse_grid.py` now tests stability as WIP drift = (last-third mean −
first-third mean) / days between the thirds' centres, < +5/day on every seed,
AND scrap share ≤ 15 %. The final-third OLS slope is still printed as
`max slope`, diagnostic only. A1 re-read: viable = qt and qtfK6 at scales 8
and 5; everything else BREAKS on scrap. Max drift anywhere in A1 is +1.8/day.
(2) **Withdrawn hypothesis noted.** The batch-min bound (35 %→2 % at scale 3) settles
it: the furnace minimum is the constraint.
(3) **qtfw arm prepared, NOT launched** (waiting on lead's qtfw A/B):
`sweep_grid.sh` exports `QTFW_SLACK_H` (default 2) and names cells
`qtfwK6s2_…` (+`m<h>` if `QTFW_MAXWAIT_H` is set, matching sim_feed's key).
Usage: `sweep_grid.sh J "qtfw" "8 5 4 3 2 1" "0 2 1 3 4" 1.00 60`.
Declared arm: it fires below the dataset batch minimum, and the user must
confirm it before it becomes the main result.
Caveat: qtfw moves scale-3 WIP a long way from the qt warm-up it resumes, so
the first third is a big transient. The drift test handles stability, but good/d
and share over 60 d will understate the steady state. Take the 90-day confirmation for
qtfw at scale 3 too.

*Compute estimate* (measured A1 walls at ~16 procs: qt/qtfK6 60-d cell 25–30 min at
scales 8/5/3, 13 min at scale 1; warm-ups 18–40 min):

| block | cells | core-h | wall @ 8 slots |
|---|---|---|---|
| warm-ups scales 4, 2 × seeds 0–4 | 10 | ~5 | ~40 min |
| qtfw 60 d × {8,5,3,1} × 5 seeds | 20 | ~9 | ~70 min |
| scales 4, 2 × {qt, qtfK6, qtfw, fifo} × 5 seeds (cr dropped: dominated everywhere) | 40 | ~17 | ~2.1 h |
| 90-d confirmations: {qt, qtfK6} × {8,5} × seeds 0,2 + qtfw × {5,3} × seeds 0,2 | 12 | ~8 | ~1 h |
| **total** | **82** | **~39** | **~5 h @ 8, ~3.3 h @ 12** |

Order once the A/B confirms: warm-ups 4/2 (can start now at no risk, since they're qt, not qtfw;
I'll hold them until A2 frees slots) → qtfw at 3 and 5 → scales 4/2 → the rest →
confirmations. A2 status: 12/48 cells done at 23:45Z; ETA ~01:30Z.

**2026-09-16 23:15Z coordinator** — CLAIM 4 slots: qt(b) warm-ups, scales 4 and 2 × seeds
0,2,1,3,4 (10), `grid_warmups.sh 4 "4 2"`, started 23:14Z, ETA ~01:00Z.
Driver `bench/results/grid/warmups/driver_42.log`. Coordinator total 12 (A2 8 + 4).
(Correction: my two previous entries were stamped 23:25Z/23:50Z; the real clock was ~23:10Z.)

**2026-09-17 00:05Z lead** — RELEASE 4. **qtfw A/B** (seed 0, 15 d, `bench/results/qtfw_ab/`), ship/d · scrap share:

| scale | qt | qtfK6 | **qtfw** | bound minb1 |
|---|---|---|---|---|
| 5 | 52.2 · 9.9% | 52.8 · 7.0% | **56.7 · 2.5%** | 49.5 · 9.1% |
| 3 | 41.3 · 35.2% | 40.6 · 29.6% | **46.2 · 24.8%** | 46.7 · 2.0% |
| 1 | 15.8 · 71.1% | 14.4 · 73.0% | **23.4 · 43.5%** | 25.2 · 38.5% |

qtfw beats every rule on BOTH good lots and scrap at every scale; near the bound at 1. **Scale 3 leaves the largest gap (24.8% vs 2.0%)** — candidate critical range for the optimiser. First tuning the rule fairly: CLAIM 4 slots (detached) `bench/tools/qtfw_tune.sh` — slack 4 h / 8 h / 4 h+maxwait 12 h at scale 3, slack 4 h at scale 5; ETA ~15 min.
Coordinator: **qtfw is confirmed — launch the qtfw arm** per your plan when slots allow (use QTFW_SLACK_H=2 for now; I will post if tuning changes the default before the scale-3/5 cells start — if tuning lands first, use its winner). 15-d/1-seed caveat stands.

**2026-09-16 23:20Z coordinator** — Ack the qtfw A/B. There are no free slots right now (I hold 12: A2 on 8,
scale-4/2 warm-ups on 4). Queued the **qtfw arm, scales 8 and 1 × seeds 0,2,1,3,4 (10 cells, 60 d,
QTFW_SLACK_H=2, cells `qtfwK6s2_…`)** with `bench/tools/grid_after.sh`: it starts at 4 jobs when
the warm-up driver (`warmups/driver_42.log`) ends and inherits those 4 slots, so the total stays 12.
Driver `bench/results/grid/qtfw81_driver.log`; ETA start ~01:00Z, done ~02:00Z.
qtfw at 5 and 3 is held for the lead's tuned default. If none is posted by the time A2 frees
slots (~01:30Z), it runs at QTFW_SLACK_H=2 and I'll note that.

**2026-09-17 00:30Z lead** — qtfw tuning round 1 (seed 0, 15 d): scale 5 — slack 2 h 56.7/d · 2.5%, 4 h 51.1/d · 2.5% (2 h wins); scale 3 — 2 h 46.2 · 24.8%, 4 h 45.4 · 22.4%, **8 h 46.8 · 16.6%**, 4 h+maxwait 12 h 46.5 · 26.7% (timeout hurts). **The best threshold is regime-dependent** — a per-scale tuned rule is the fair baseline, and "needs retuning per regime" is itself a result. Round 2 CLAIM 4 slots (detached, `bench/tools/qtfw_tune2.sh`, ETA ~20 min): slack 8 h at scales 5 and 1, 16 h at 3, plus first crit v3 cell (scale 3, CRIT_UNDERFILL_W=300, qtfw 8 h fallback).
Coordinator: for the scale 5/3 qtfw cells, hold for round 2 (~00:50Z). Provisional per-scale thresholds: 2 h at scales ≥ 5, 8 h at ≤ 3 (name cells qtfwK6s{h}).

**2026-09-17 00:00Z lead** (clock-corrected) — qtfw tuning round 2 (seed 0, 15 d), ship/d · share: scale 5 — 2 h 56.7 · 2.5%, 8 h 53.5 · 2.0% (but WIP end +55 ⇒ ~equal output incl. WIP); scale 3 — 8 h 46.8 · 16.6%, 16 h 46.4 · 15.8% (flat past 8 h); scale 1 — 2 h and 8 h identical (43.5%). **Decision: one threshold for all scales, QTFW_SLACK_H=8** (no per-scale tuning; cells `qtfwK6s8`). Coordinator: use s8 for ALL remaining qtfw cells; the queued scale 8/1 block at s2 is fine to keep as a sensitivity pair if already started, otherwise switch it to s8. crit v3 (scale 3, underfill 300) still running (slower: CP-SAT + under-min).

**2026-09-16 23:58Z coordinator** — qtfw default **s8** adopted. The queued s2 block for scales 8/1 had
not started, so I cancelled it: **no s2 cells exist**. Every qtfw cell is now `qtfwK6s8_…`.
Armed **3 detached lanes** (`bench/tools/grid_lane.sh`, 4 jobs each, so the total stays ≤ 12).
Each lane waits for a running driver to end, takes its slots, runs its blocks in order, and
stops on any rc≠0 or REBUILT-WARMUP. Events, including RELEASE-READY and END lines, go to
`bench/results/grid/chain.log`, and each block's driver is `bench/results/grid/lane_<L>_b<i>.log`.

| lane | waits for (ETA) | block 1 | block 2 | lane done |
|---|---|---|---|---|
| A | scale 4/2 warm-ups (~00:50Z) | qtfw s8 × {8,1} × 5 seeds, 60 d (10) | qt, qtfK6 × {4,2} × 5 seeds, 60 d (20) | ~03:50Z |
| B | A2 seed-4 run (~01:05Z) | qtfw s8 × {5,3} × 5 seeds, 60 d (10) | qtfw s8, fifo × {4,2} × 5 seeds, 60 d (20) | ~04:20Z |
| C | A2 seeds-1/3 run (~00:40Z) | qt, qtfK6 × {8,5} × seeds 0,2, 90 d (8) | qtfw s8 × {8,5,3} × seeds 0,2, 90 d (6) | ~03:15Z |

That is 74 cells in all. Each lane start inherits a finished block's slots, so treat these as
CLAIM-by-succession of the current 12. Final RELEASE 12 when all three lanes log "lane done"
(~04:30Z). The lanes can't write to this file; I (or lead) will transcribe chain.log here.

**2026-09-17 00:58Z lead** — **crit v3 first win** (scale 3, seed 0, 15 d, `bench/results/crit_ab/cqt3r0c_s0_v3u300.json`): ship/d · scrap/d · share — qt 41.3 · 22.4 · 35.2%; qtfw s8 46.8 · 9.3 · 16.6%; **crit v3 45.8 · 6.2 · 11.9%** (WIP end 1618 vs 1556 ⇒ output ≈ level). Scrap −33% vs the best rule in the critical range. One seed, 15 d; 68 min wall (CP-SAT 2 s × 3 families). CLAIM 4 slots (detached, `bench/tools/crit3_ab.sh`, ETA ~75 min): crit v3 scale 3 s2 + qtfw s8 scale 3 s2, crit v3 scales 5 and 1 s0.
Coordinator: please note crit v3 cells are ~13× a rule cell; a 60-d grid arm at 5 seeds × 4 scales ≈ 20 × ~4.5 h ≈ 90 core-h. Propose a cheaper crit grid design (e.g. seeds 0,2 × scales 5,4,3,2 first; or I cut the budget to 1 s / replan 60 min) and log it; do not launch yet.

**2026-09-17 01:00Z coordinator** — chain.log transcript: A2 finished, 48/48 cells rc=0. Scale 4/2
warm-ups 10/10 rc=0. Lane B took A2b's 4 slots at 00:42Z (qtfw s8 × {5,3}); lane A took the
warm-ups' 4 at 00:48Z (qtfw s8 × {8,1}); lane C took A2's 4 at 00:48Z (90-day qt/qtfK6 × {8,5}).
RELEASE of the A2 and warm-up slots is done by succession: I still hold 12, all lanes, same ETAs as the 23:58Z table.

**crit grid arm, proposal (not launched):**
- **Budget cut A/B first: yes.** It is cheap (15 d, from the same checkpoints) and the arm
  costs 13× a rule cell. The A/B needs two numbers: scrap share and good/d within noise of v3 defaults,
  and wall time. If CRIT_BUDGET_S=1 with CRIT_PLAN_S=3600 is ≥2× faster with scrap within ~1 pt
  of defaults at scale 3 on seeds 0 and 2, the grid uses it (key it into the cell name, e.g.
  `critU300b1p3600`). If not, the grid uses defaults. crit_sched sets num_workers=1, so one process is
  one core and the ≤16 count stays honest.
- **Cells:** crit × scales {3, 2, 4, 5} × seeds 0, 2, 60 d, from the same qp050b warm-ups, run in
  that order (critical range first). That's 8 cells. Scale 8 is left out (nothing to fix there). Scale 1 goes in
  only if the replication shows a gain there. Each crit cell's paired rows are qtfwK6s8 and qt
  on the same seed/scale, which A1/A2 and lanes A/B already cover.
- **Cost:** defaults ~4.5 h/cell × 8 = **~36 core-h**. With the budget cut at ~2× faster, ~18.
- **Schedule:** lanes finish ~03:15Z (C), ~03:50Z (A), ~04:20Z (B).
  - Option 1, defaults: start 4 cells (scales 3, 2) as lane C frees at ~03:15Z and 4 more (scales 4, 5)
    as lane A frees at ~03:50Z → done **~08:30Z**.
  - Option 2, budget-cut version passes: same slots, done **~06:00Z**.
  - If you give me your 4 crit slots after the replication (~02:15Z), scales 3/2 start then:
    first readable crit-vs-qtfw rows at scale 3 around **06:45Z** (defaults) or ~04:30Z (cut).
- **Caveats:** crit resumes a qt-built fab, so the first-third transient is bigger than
  for qtfw. Keep the 60-d share/good-d numbers labelled "includes transient". Confirm the winner
  at scale 3 on 90 d (2 cells, ~13.5 core-h at defaults). n=2 seeds is a readable signal,
  not a result, so extend to seeds 1, 3, 4 only at the scale where crit and qtfw differ by more than the seed spread.
- **Launch:** `sweep_grid.sh` needs a `crit` name tag that includes CRIT_UNDERFILL_W and, if
  cut, the budget/plan values. I'll add it when you post the A/B outcome.

**2026-09-17 01:10Z coordinator** — crit cell-name tag added to `sweep_grid.sh`:
`critK{QTF_LOOKAHEAD}s{QTFW_SLACK_H}U{CRIT_UNDERFILL_W}B{CRIT_BUDGET_S}P{CRIT_PLAN_S}`.
Defaults give `critK6s8U300B2P1800`, the cut gives `critK6s8U300B1P3600`. The sweep exports
CRIT_UNDERFILL_W=300 when unset, because crit_sched reads unset as "no under-min pricing" and the
name would otherwise lie. It refuses to run if any other CRIT_* knob (HORIZON/SLOTS/LOOKAHEAD/
PAD/HOLD_MAX/FAMILIES) is set. Ready for either A/B verdict. Planned launch on your 4 slots:
`QTFW_SLACK_H=8 [CRIT_BUDGET_S=1 CRIT_PLAN_S=3600] sweep_grid.sh 4 crit "3 2" "0 2" 1.00 60`.
Scale 1 waits on the replication result. (The running lane sweeps hold the old script inode, so the edit doesn't touch them.)

**2026-09-17 02:00Z lead** — crit v3 replication, partial: **scale 1 s0 crit v3 LOSES** (22.3/d · 53.8% share vs qtfw s8 23.7 · 43.7%) ⇒ scale 1 stays out of the crit arm. qtfw s8 scale 3 s2 = 49.2/d · 13.5% (baseline for the pending crit s2 cell). crit scale 3 s2 and scale 5 s0 still running (~1 h in).

**2026-09-17 02:05Z lead** — crit v3 replication complete (15 d), ship/d · share, qtfw s8 vs crit v3: scale 3 s0 46.8 · 16.6% vs **45.8 · 11.9%**; scale 3 s2 49.2 · 13.5% vs **49.6 · 8.3%**; scale 5 s0 53.5 · 2.0% vs 52.8 · 8.8% (loses); scale 1 s0 23.7 · 43.7% vs 22.3 · 53.8% (loses). **crit wins in the critical range (scale 3, both seeds, scrap −30–40%) and loses outside it.** Hypothesis: underfill weight 300 too cheap where windows are loose. CLAIM 4 (detached `bench/tools/crit_budget_ab.sh`, ETA ~70 min): budget cut B1/P3600 at scale 3 s0,s2; underfill 600 at scales 5 and 3 s0.
Coordinator: crit arm scales = {4, 3, 2} (not 5, not 1) until the u600 result; launch waits for the budget verdict.

**2026-09-17 03:08Z lead** — Budget-cut verdict (scale 3, 15 d): B1/P3600 is 2.9× faster but share rises 11.9→13.7% (s0) and 8.3→12.4% (s2) — **fails the ≤~1 pt rule; crit arm keeps defaults (B2/P1800)**. RELEASE 2 of 4. CLAIM 2 slots: `bench/tools/crit_arm_x3.sh` = sweep_grid.sh 2 crit "3" "0 2" 1.00 60 (critK6s8U300B2P1800), ETA ~4.5 h ⇒ ~07:40Z, driver `bench/results/grid/crit_x3_driver.log`. u600 A/B (scales 5, 3) still on the other 2; when it ends I launch crit scale 2 s0,s2 there. Scale 4 crit cells go to the coordinator as lanes free.

**2026-09-17 03:40Z lead** — Underfill weight A/B (seed 0, 15 d), ship/d · share: scale 5 — qtfw s8 53.5 · 2.0%, crit u300 52.8 · 8.8%, **crit u600 50.0 · 2.1%** (WIP +52 ⇒ output ≈ level); scale 3 — qtfw s8 46.8 · 16.6%, **crit u300 45.8 · 11.9%**, crit u600 46.5 · 13.5%. No single weight wins both: u600 ties qtfw at 5, u300 is best at 3. Same regime-dependence as qtfw threshold — to be reported, not hidden. Grid arm stays u300 (critical range). RELEASE 2 (u600 done). CLAIM 2: `bench/tools/crit_arm_x2.sh` (crit scale 2 s0,s2, 60 d), ETA ~4.5 h.
Idea queued (not started): make the underfill price state-dependent (e.g. scale with the family queue length or blown-window risk) so one setting works across regimes.

**2026-09-17 03:25Z coordinator** — chain.log transcript: lane A b1 (qtfw s8 × {8,1}) END 01:59Z,
10 cells, then b2 (qt, qtfK6 × {4,2}) started. Lane B b1 (qtfw s8 × {5,3}) END 02:04Z, 10 cells, then b2
(qtfw s8, fifo × {4,2}) started. Lane C b1 (90-d qt/qtfK6 × {8,5}) END 02:24Z, 8 cells, then b2 (90-d
qtfw × {8,5,3}) started. No STOP events. Count at 03:16Z: 16 grid processes (my 12 + lead 4),
plus the long-running dashboard `sim_feed --kafka` feed, which is not ours.
**Queued the lead's request** as lanes D and E (2 jobs each). Both take lane C's 4 slots when lane C b2 ends (~04:15Z):
D = `crit` × scale 4 × seeds 0,2, 60 d (`critK6s8U300B2P1800`), ETA ~08:45Z;
E = qtfw s8 × {4,2} × seeds 0,2, 90 d, ETA ~05:40Z. My total stays 12.
**T6 draft:** `docs/notes/2026-09-17-where-rules-break.md`. **Partial grid:** `bench/results/grid/GRID_SUMMARY.txt`.
**Trap found, please read:** qtfw's BREAKS at 4/3/2 are drift, and the drift is WIP re-equilibrating
*upward* from the qt warm-up (qtfw keeps lots alive: scale 3 ~1500 → ~1830 plateau). The 60-d scale-3
qtfw share (9.6 %) is optimistic, and the 90-d seed-0 cell reads 12.0 % with drift +2.7. crit resumes the same
qt warm-ups, so it has the same bias, probably larger. **Proposal:** build qtfw-warmed checkpoints
(`--warmup-dispatcher qtfw`, key `_qp050bf6w8` exists) for scales 4/3/2 × 5 seeds (15 warm-ups,
~50 min each ≈ 12 core-h) and run the headline qtfw rows plus the crit arm from them. That changes the paired
baseline: rules would then resume a qtfw fab, so plain-rule rows keep the qt warm-up. Lead: agree? The
running crit cells (qt-warmed) stay valid as paired vs qtfw on the same warm-up, but should be labelled.

**2026-09-17 03:50Z lead** — crit v4 built (not yet A/B-ed): `CRIT_MODEL=family` — per-group optional batches on a shared cumulative furnace capacity (removes per-tool symmetry). With under-min pricing the per-tool model (v3) returned **UNKNOWN for Diffusion_FE_94 at 2 s at scales 5 and 3** (crit_debug) — so v3 ran FE_94 on stale plans / qtfw fallback; its scale-3 win came mostly from the other two families. v4 solves FE_94 FEASIBLE in 2 s. Also `CRIT_UNDERFILL_MODE=load` (price scales with family backlog). Tests `test_crit_sched.py` 4/4.
Reply to coordinator: **agree to qtfw-warmed checkpoints** (scales 4,3,2 × 5 seeds, `_qp050bf6w8`). Priority order for slots as lanes free: (1) qtfw warm-ups scale 3 seeds 0,2 FIRST (needed for the v4 A/B), then the rest; (2) cancel lane D (crit v3 scale 4) — v4 will supersede v3 on the fair warm-ups; keep lane E; (3) my running crit v3 x3/x2 cells stay, labelled "paired vs qtfw on qt warm-ups". When scale-3 s0,s2 qtfw warm-ups exist I will A/B qtfw s8 vs crit v4 (u300 fixed, u300 load) from them.

**2026-09-17 03:22Z coordinator** — Per lead 03:50Z (the lead's clock label runs ahead of mine):
- **Lane D cancelled** before it started, so no crit v3 scale-4 cells exist.
- **Lane E kept:** qtfw s8 × {4,2} × seeds 0,2, 90 d, on 2 of lane C's slots, ETA ~05:40Z.
- **qtfw-warmed checkpoints + re-run, chained:**
  - `grid_warmups.sh` takes `WARM_RULE=qtfw` (K6/s8 pinned). Checkpoint name
    `SMT2020_LVHM_seed{s}_qtfw_Demand_day90_cqt{X}r0c_qp050bf6w8_h270.ckpt`, checked via ckpt_path.
  - `sweep_grid.sh` takes `WARM=qtfw` → cells `qtfwK6s8Wqtfw_…`.
  - Lane W1 (2 jobs, takes lane C's other 2 slots at ~04:10Z): warm-ups x3 s0,s2 (**first**) →
    x4, x2 s0,s2 → cells qtfw from qtfw warm-ups × {3,4,2} × s0,s2, 60 d. ETA READY x3 ~05:00Z,
    lane done ~09:00Z.
  - Lane W2 (4 jobs, takes lane A's slots at ~04:35Z): warm-ups {3,4,2} × s1,3,4 → cells
    × s1,3,4, 60 d. Done ~08:30Z.
  - The two lanes split seeds, so no two processes ever build the same checkpoint.
- **READY flag:** `grid_ready_flag.sh` appends "QTFW-WARM x3 s0,s2 READY" to this Log (and
  chain.log) once both checkpoint files exist AND their warm-ups log rc=0.
- **Auto analysis:** `grid_auto_analyse.sh` re-runs analyse_grid.py into
  `bench/results/grid/GRID_TABLE_auto.txt` and logs it when lanes A B C E W1 W2 are done. GRID_SUMMARY + lab note
  get rewritten from it on my next invocation. The headline qtfw rows at 4/3/2 will come from the `Wqtfw` cells.
- **Slots by succession:** A→W2 (4), B (4, ends ~04:20Z then idle), C→E+W1 (2+2). I stay ≤ 12,
  dropping to 8 when B ends. Lane B's 4 are free for the lead after ~04:20Z.

**2026-09-17 04:22Z lead** — Lane B released 4 (chain.log 04:20Z); lane C not yet ended so W1 (qtfw warm-ups s0,s2) has not started. To avoid racing W1, the crit v4 A/B will use the **scale 3 qtfw warm-ups for seeds 1 and 3 that W2 is building now** (ETA ~05:00Z). CLAIM lane B's 4 slots + my own 4 (crit v3 x3/x2 60-d cells will be stopped at A/B launch — superseded by v4, and on biased qt warm-ups). A/B = qtfw s8 | v4 u300 | v4 u300 load × seeds 1,3, 15 d (6 procs).

**2026-09-17 04:41Z coordinator (auto)** — QTFW-WARM x3 s0,s2 READY: bench/snapshots/SMT2020_LVHM_seed{0,2}_qtfw_Demand_day90_cqt3r0c_qp050bf6w8_h270.ckpt (lane W1).

**2026-09-17 04:55Z lead** — Stopped crit v3 60-d cells (x3, x2; superseded by v4; partial logs left, no JSON). RELEASE 4 of those; CLAIM 6 (lane B's 4 + 2): `bench/tools/crit_v4_ab.sh` on the qtfw-warmed scale-3 checkpoints from W1 (seeds 0,2): qtfw s8 | crit v4 u300 | crit v4 u300 load, 15 d. ETA ~75 min, `bench/results/crit_v4_ab/`. 2 lead slots idle.
