# Lab notes — where rules-based dispatch breaks down

**2026-09-16 evening to 2026-09-17 morning.** Follows
[the scrap band](2026-09-16-scrap-band.md). SMT2020 LVHM in PySCFabSim,
scrap on the first queue-time violation, Demand batching, `QT_PROMOTE_FRAC=0.50`.

Numbers: [`bench/results/grid/GRID_SUMMARY.txt`](../../bench/results/grid/GRID_SUMMARY.txt)
(the breakdown map; full table in `GRID_TABLE_auto.txt`), and the A/Bs in
`bench/results/batch_tier_ab/`, `qtf_ab/`, `bound_ab/`, `qtfw_ab/`, `crit_ab/`,
`crit_v4_ab/`. Coordination log: `agentchats.md`.

**Provisional marks.** Numbers marked *(15 d)* come from paired 15-day A/Bs from one
warmed checkpoint. That is under one cycle time: good for direction, not for size.
*(n=1)* and *(n=2)* give the seed count. Grid numbers are 5 seeds, 60-day windows,
unless marked otherwise.

---

## Result

| scale | plain qt (dataset-faithful) | qtfw, under-min exception |
|---|---|---|
| 8 | 56.2 lots/d · 2.0 % scrap — viable | 57.3 · 0.4 % — viable |
| 5 | 54.2 · 7.6 % — viable | 55.0 · 2.4 % — viable |
| 4 | 47.1 · 18.8 % — **breaks** | 52.0 · 5.5 % — viable |
| 3 | 37.4 · 34.4 % — breaks | 49.4 · 12.2 % — **breaks** (one seed 15.8 %) |
| 2 | 26.3 · 54.1 % — breaks | 43.9 · 23.5 % — breaks |
| 1 | 14.0 · 75.4 % — breaks | 29.5 · 45.1 % — breaks |

Viable = WIP drift < +5/day on every seed and scrap share ≤ 15 % on every seed.
qtfw rows at scales 4, 3 and 2 resume qtfw-built warm-ups (see §6).

- **The qt family is viable down to window scale 5. Plain qt breaks at 4.** It breaks on
  scrap, not on flow: under scrap-on-first no cell's WIP ran away.
- **With an under-min batch exception (qtfw), rules hold to scale 4 and break at 3–2.**
  That is the critical range.
- fifo and cr are not viable anywhere (fifo scraps up to 22 % of lots even at scale 8).
  At scale 1 nothing is viable.
- **Optimiser status: no scheduler version yet beats the tuned rule on fair
  checkpoints.** crit v3's earlier scale-3 win was an artifact of the warm-up (§7).

> **Needs user sign-off.** qtfw fires furnace batches below the dataset's
> `batch_min`. Fabs do run a minimum batch with a window exception, so the rule is
> realistic, but it is a deviation from SMT2020 as published. Until the user confirms,
> plain qt is the dataset-faithful main result and qtfw is a declared arm.

## 1. A bug in the queue-time rule, fixed first

`qt`'s window tier never reached batch formation, and where it did it was
inverted (`f1b6811`). The fix is now the default. Checkpoints warmed under it carry `b`,
and `QT_BATCH_TIER=0` reproduces the old rows. At scale 5 it cuts violations by 15–20 %; at scale 1 it
changes nothing *(15 d, n=2)*. **Every qt row published before 2026-09-16 predates it.**
All 30 grid warm-ups (scales 8/5/4/3/2/1 × 5 seeds) were rebuilt under the fix.

## 2. How the grid is measured

Every rule at a given seed and scale resumes the same day-90 warm-up, so rows are
paired. Metrics:
- **Good lots shipped/day.**
- **Scrap share** = scrapped / (shipped + scrapped).
- **Cycle time** of shipped lots. It is survivor-biased (scrap empties the fab and CT falls), so it is never read alone.
- **WIP drift.**
- A conservation check, (shipped + scrapped + ΔWIP) / starts, which is 1.00 in every cell.

On-time delivery is not reported, because scrapped lots are never late.

**Stability test.** The first test was the final-third WIP slope < +5/day. On
60-day windows it flagged qt cells whose WIP *ended below where it started*:
10-day WIP swings of ±100–150 lots make a 20-day slope worth ±10/day. It was replaced,
by agreement and before any conclusion depended on it, with drift = mean WIP of
the last third minus the first third, per day between the thirds' centres.

## 3. Feed-the-batch (`qtf`) did not hold

`qtf` promotes upstream lots that would complete a waiting partial batch. At
lookahead 6 the 15-day A/B cut scale-5 scrap by 22–30 % *(15 d, n=2)*. On the grid it
is level with qt (scale 5: 7.9 % vs 7.6 %; scale 4: 17.4 % vs 18.8 %).

## 4. The constraint is the furnace minimum batch

A bound run set every `batch_min` to 1 under qt *(15 d, n=1)*:

| scale | qt | batch_min = 1 |
|---|---|---|
| 5 | 52.2/d · 9.9 % | 49.5 · 9.1 % |
| 3 | 41.3 · 35.2 % | **46.7 · 2.0 %** |
| 1 | 15.8 · 71.1 % | 25.2 · 38.5 % |

Tight-window scrap is overwhelmingly lots waiting for batch partners under a hard
minimum at the diffusion furnaces (FE_94, FE_120, BE_123). An earlier hypothesis,
that the violations begin upstream, was withdrawn. At scale 5 underfilled runs cost
capacity (−2.7/d), so firing early is a real capacity-versus-scrap trade.

## 5. `qtfw`: fire an underfilled batch before a window closes

`qtfw` = qtfK6 + fire an underfilled same-step+part group when a member has
less than `QTFW_SLACK_H` of window left. Threshold tuning *(15 d, n=1)*:
- scale 5: 2 h and 8 h tie once end WIP is counted;
- scale 3: 8 h beats 2 h, and 16 h adds nothing;
- scale 1: 2 h and 8 h are identical.

**One threshold for all scales, 8 h**, with no per-scale tuning. On the grid it beats every plain rule
on both good lots and scrap at every scale from 5 down (table above).

## 6. Warm-up bias: qtfw on a qt-built fab

The first qtfw grid cells resumed the qt warm-ups. qtfw keeps alive lots that the qt
fab would have scrapped, so WIP climbs to a new, higher plateau during the window
(scale 3: about 1,500 → 1,830 lots over roughly 40 days). Two consequences:

- the drift test flagged qtfw as breaking at 4, 3 and 2 when it was re-equilibrating;
- the 60-day scrap share was **optimistic** (the extra survivors had not yet been scrapped).

Headline qtfw rows at scales 4, 3 and 2 were therefore re-run from **qtfw-built warm-ups**
(15 new checkpoints, `_qp050bf6w8`), 5 seeds:

| scale | qtfw on qt warm-up | qtfw on qtfw warm-up |
|---|---|---|
| 4 | 50.9/d · 5.7 %, drift +6.7 (flagged) | **52.0 · 5.5 %, drift +2.9, viable** |
| 3 | 47.2 · 9.6 %, drift +6.7 | **49.4 · 12.2 %, drift +1.8** |
| 2 | 41.2 · 21.8 %, drift +5.4 | **43.9 · 23.5 %, drift +3.4** |

On the fair warm-ups qtfw is stable at every scale, ships more, and scraps somewhat
more at 3 and 2 than the biased cells showed. 90-day cells on the qt warm-ups agree
(scale 3: 12.0 %, n=2).

## 7. The critical-section scheduler (`crit`): not yet better than qtfw

CP-SAT over a rolling horizon for the furnace families that close windows; the rest of
the fab runs the rule.

- **v1** lost to qtfK6 at scale 5 (scrap 7.8 vs 4.0 lots/d) *(15 d, n=2)*. Plans came back
  MODEL_INVALID: a furnace busy past the horizon got an empty start domain, so whole
  families went unplanned. It also held furnaces for inbound members on optimistic ETAs
  (600k+ member-wait re-offers). Wall time was 12–20× qt.
- **v2** fixed the model, narrowed the candidates and limited holds to members due within 45 min. It still lost at
  scale 5 (5.9 vs 4.0/d) *(15 d, n=2)*. Holds still dominated.
- **v3** added a price on under-min batches (weight 300) with qtfw as fallback. **On the qt warm-ups** it
  beat qtfw at scale 3 on both seeds: 11.9 % vs 16.6 % (s0), 8.3 % vs 13.5 % (s2)
  *(15 d, n=2)*. It lost at scale 5 (8.8 % vs 2.0 %) and scale 1 (53.8 % vs 43.7 %). A higher weight
  (600) tied qtfw at 5 but gave back most of the scale-3 gain. Cutting the solver budget made it 2.9× faster and
  worse (+1.8 to +4.1 points), so the cut was rejected.
- **v3 on the fair qtfw warm-ups, same scale and seeds: the win is gone.** s0: qtfw 47.7/d ·
  7.0 % vs v3 46.9 · **21.6 %**; s2: qtfw 47.5 · 14.5 % vs v3 49.9 · 14.2 % (a tie)
  *(15 d, n=2)*. **The earlier v3 win was an artifact of resuming from the qt warm-up.**
  During that warm-up's upward transient, a scheduler that fires under-min batches looked
  better than it is. The per-tool model also returned UNKNOWN for FE_94 at the 2 s budget,
  so v3 ran that furnace largely on stale plans or the qtfw fallback.
- **v4** plans per batch group on shared furnace capacity. On fair warm-ups at scale 3 *(15 d, n=2)*:
  - with holds, s0 21.2 % and s2 16.1 %, against qtfw's 7.0 % and 14.5 %;
  - without holds, s0 13.3 % and s2 13.0 %: it loses s0 and edges s2 by 1.5 points on one seed.

**No scheduler version (v1–v4, holds on or off, weight 300 or 600, load pricing) beats tuned
qtfw on fair checkpoints.** Holding a furnace for arriving members has hurt in
every version, as the entrance hold heuristic did before.

## What's next

1. **User decision:** accept the under-min exception (qtfw) as the realistic
   rules-based baseline, or keep plain qt as the only main result.
2. **crit v5 (lead):** a conservative hybrid. qtfw everywhere; the CP-SAT plan overrides only
   where it would save a windowed lot that qtfw would blow; no speculative holds.
   A/B on qtfw-built warm-ups at scales 3 and 2, seeds 0 and 2, then 60 days × 5 seeds if it wins.
   Every optimiser comparison from here uses the same fair warm-ups as its baseline.
3. **Grid gaps:** a load axis (`--starts-scale`) at scales 4 and 3, run from matching warm-ups;
   qtfw-warmed scale 1 (not built); rework as a sensitivity arm.
