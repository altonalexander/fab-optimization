# Lab notes — where the rules break, and what fixes it

**2026-09-16 evening to 2026-09-17 early morning.** Follows
[the scrap band](2026-09-16-scrap-band.md). SMT2020 LVHM in PySCFabSim,
scrap on the first queue-time violation, Demand batching, `QT_PROMOTE_FRAC=0.50`.

Numbers: [`bench/results/grid/GRID_SUMMARY.txt`](../../bench/results/grid/GRID_SUMMARY.txt)
(the grid), `bench/results/batch_tier_ab/`, `qtf_ab/`, `bound_ab/`, `qtfw_ab/`,
`crit_ab/`. Coordination log: `agentchats.md`.

**Provisional marks.** Rows marked *(15 d)* are paired 15-day A/Bs from a warmed
checkpoint: under one cycle time, so good for direction, not for size. Rows
marked *(n=1)* or *(n=2)* have one or two seeds. Grid rows are 60-day windows
unless marked 90 d.

---

## 1. A bug in the queue-time rule, fixed first

`qt`'s window tier never reached batch formation, and where it did it was
inverted (`f1b6811`). The fix is now the default. Every checkpoint warmed under
it carries a `b` suffix, and `QT_BATCH_TIER=0` reproduces the old rows. At scale 5
it cuts violations by 15–20 %. At scale 1 it changes nothing *(15 d, n=2)*.
**Every qt row published before 2026-09-16 predates it.** The grid below
rebuilt all its warm-ups under the fix: 30 checkpoints, scales 8/5/4/3/2/1 × 5 seeds.

## 2. The grid: plain dispatch rules

Every rule resumes the same qt warm-up for its seed and scale, so rows are paired.
Metrics: good lots shipped/day; scrap share = scrapped / (shipped + scrapped);
cycle time of shipped lots (survivor-biased, so never read alone); WIP stability.

**Stability test.** Stability was first defined as the final-third WIP slope < +5/day.
On 60-day windows that flagged qt cells whose WIP ended *below* where it
started: 10-day WIP swings of ±100–150 lots make a 20-day slope ±10/day
noise. Replaced (agreed before any result depended on it) by drift = mean WIP
of the last third minus the first third, per day, < +5 on every seed. **Viable**
= stable AND scrap share ≤ 15 %.

5 seeds, 60 d:

| scale | qt | qtfK6 | fifo | cr |
|---|---|---|---|---|
| 8 | 56.2/d · 2.0 % | 57.6 · 1.3 % | 53.7 · 12.7 % | 45.7 · 27.9 % |
| 5 | 54.2 · 7.6 % | 53.2 · 7.9 % | 46.5 · 25.9 % | 42.3 · 32.0 % |
| 3 | 37.4 · 34.4 % | 37.7 · 33.4 % | 36.0 · 37.9 % | 32.7 · 43.7 % |
| 1 | 14.0 · 75.4 % | 14.7 · 73.9 % | 14.4 · 74.6 % | 13.7 · 75.9 % |

At scale 4, qt reads 46.8/d at 20.2 % (n=3); at scale 2, 25.9/d at 55.0 % (n=3).

**Rules-based dispatch is viable at scales 8 and 5 and breaks at 4 and
tighter, on scrap, not on flow.** fifo and cr are not viable anywhere under
queue-time constraints. At scale 1 the rule makes no difference.

## 3. Feed-the-batch (`qtf`) did not hold

`qtf` promotes upstream lots that would complete a waiting partial batch.
At K=6 the 15-day A/B cut scale-5 scrap by 22–30 % *(15 d, n=2)*. On the 60-day
grid, 5 seeds, it is level with qt (7.9 % vs 7.6 %). The short A/B picked up
noise, which is why winners go to the grid before anyone reports them.

## 4. The constraint is the furnace minimum batch

A bound run set every `batch_min` to 1 under qt *(15 d, n=1)*:

| scale | qt | batch_min = 1 |
|---|---|---|
| 5 | 52.2/d · 9.9 % | 49.5 · 9.1 % |
| 3 | 41.3 · 35.2 % | **46.7 · 2.0 %** |
| 1 | 15.8 · 71.1 % | 25.2 · 38.5 % |

Tight-window scrap is overwhelmingly lots waiting for batch partners under a
hard minimum. An earlier hypothesis, that the violations begin upstream of the
furnaces, is withdrawn. At scale 5 underfilled runs cost capacity (−2.7/d), so this
is a real capacity-versus-scrap trade.

## 5. `qtfw`: fire an underfilled batch when a window is about to close

`qtfw` = qtfK6 + fire an underfilled same-step+part group when a member has
less than `QTFW_SLACK_H` of window left. Threshold tuning *(15 d, n=1)*: at scale 5,
2 h and 8 h tie once WIP is counted; at scale 3, 8 h beats 2 h and 16 h adds nothing;
at scale 1 they are identical. **One threshold for all scales: 8 h.**

> **Deviation needing user sign-off.** Firing below the dataset's
> `batch_min` departs from SMT2020 as published. Fabs do run a minimum batch with
> exceptions, but the main result cannot use it until the user
> confirms. Until then qtfw is a declared arm beside the plain rules.

Grid, 60 d:

| scale | n | qtfwK6s8 | best plain rule |
|---|---|---|---|
| 8 | 5 | 57.3/d · 0.4 % | qtfK6 57.6 · 1.3 % |
| 5 | 5 | **55.0 · 2.4 %** | qt 54.2 · 7.6 % |
| 4 | 2 | **50.4 · 4.9 %** | qt 46.8 · 20.2 % |
| 3 | 5 | **47.2 · 9.6 %** | qtfK6 37.7 · 33.4 % |
| 2 | 2 | **42.1 · 20.0 %** | qt 25.9 · 55.0 % |
| 1 | 5 | **29.5 · 45.1 %** | qtfK6 14.7 · 73.9 % |

qtfw is better on both good lots and scrap at every scale from 5 down.
**Caveat on the tight scales:** qtfw keeps alive lots that the qt warm-up would have
scrapped, so WIP climbs from the qt level to a higher plateau (scale 3: about
1,500 → 1,830 over roughly 40 days). The drift test flags that as BREAKS at 4/3/2,
and the 60-day scrap share is optimistic. The one 90-day scale-3 cell flattens (drift
+2.7) and reads 12.0 %, not 9.6 %. Headline qtfw numbers at scale 4 and tighter need
qtfw-warmed checkpoints or 90-day windows. On the evidence so far, qtfw is still
viable at scale 3 (90 d, n=1) and breaks at 2 and 1 on scrap.

## 6. The critical-section scheduler (`crit`)

CP-SAT over a rolling horizon for the batch families that close windows
(Diffusion_FE_94, FE_120, BE_123). The rest of the fab runs the best rule.

- **v1 lost** to qtfK6 at scale 5 (scrap 7.8 vs 4.0/d, seed 0) and was flat at scale 1
  *(15 d, n=2)*. Two causes. First, scale-5 plans failed as MODEL_INVALID: a
  furnace busy past the horizon got an empty start domain, so whole families
  went unplanned and fell back mid-plan. Second, furnaces were held for inbound members
  whose ETAs were optimistic (600k+ member-wait re-offers). Its wall time was 12–20× qt.
- **v2** fixed the model, planned only lots waiting or one step away, held only for
  members due within 45 minutes, and kept the last plan on solver failure. **It still lost at
  scale 5** (5.9 vs 4.0/d) *(15 d, n=2)*. Holds still dominated its counters.
- **v3** prices under-min batches (`CRIT_UNDERFILL_W`) with qtfw s8 as the
  fallback. At scale 3 **it beats qtfw s8 on both seeds**: scrap share 11.9 % vs
  16.6 % (s0) and 8.3 % vs 13.5 % (s2), with output about level *(15 d, n=2)*. **It loses at
  scale 5** (8.8 % vs 2.0 %) **and at scale 1** (53.8 % vs 43.7 %) *(15 d, n=1)*.
- **Underfill price depends on the regime.** At u600 crit ties qtfw at scale 5
  (2.1 % vs 2.0 %) but gives back part of the scale-3 gain (13.5 % vs 11.9 % at u300)
  *(15 d, n=1)*. No single weight wins both. This is the same pattern as the qtfw
  threshold, and it is reported as found. A state-dependent price is queued, not built.
- A budget cut (1 s solves, 60-minute replans) was 2.9× faster but raised scale-3
  share by 1.8–4.1 points, so it was rejected.

Crit grid arm, running: `critK6s8U300B2P1800` × scales 4, 3, 2 × seeds 0 and 2, 60 d.
Scale 5 and scale 1 are excluded on the A/B evidence. Each cell takes about 4.5 h.

## Where this leaves the three goals

1. **Realistic rules-based plan:** qt with the batch-tier fix. It is viable to scale 5.
   With a declared under-min exception (qtfw, 8 h) it is viable to about scale 3.
2. **Where rules break:** plain rules break at scale 4 (scrap 20 %). qtfw moves
   the break to between 3 and 2. At scale 1 nothing viable exists.
3. **Optimisation that beats the rules in the critical range:** crit v3 at scale 3
   is the only candidate so far *(15 d, n=2)*. The 60-day grid arm is running. It
   loses outside that range, so any claim has to be scoped to it.
