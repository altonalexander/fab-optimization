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
| T1 | `qtf` feed-the-batch rule + test + paired A/B | lead | in progress |
| T2 | critique plan; design the grid (axes, cells, compute, ETA) | coordinator | open |
| T3 | regenerate warm-ups/baselines under batch fix (qp050b) for grid | coordinator | open (needs T2) |
| T4 | critical-section CP-SAT scheduler | lead | open (after T1) |
| T5 | grid runs + analysis script + SUMMARY | coordinator | open |
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
