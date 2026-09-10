# Starts grid — 60 days from the day-90 checkpoint (2026-09-09)

`compare.py --days 150 --warmup-days 90 --starts-scale S --rules R`, headless,
one process per cell. `thr/d@end` is the trailing-day throughput averaged over
the last 10 days; WIP is the mean over the first and last five days of the
window; queues are the longest family queue at the end.

| x    | rule  | lots/60d | thr/d @end | util % | on-time % | tardiness | WIP first5d → last5d | longest queues at end |
|------|-------|---------:|-----------:|-------:|----------:|----------:|---------------------:|-----------------------|
| 1.00 | fifo  | 3469 | 54.7 | 80.5 | 98.3 |  56 | 2062 → 2031 | Litho_BE_110 64 |
| 1.00 | cr    | 3287 | 50.9 | 81.0 | 99.8 |   5 | 2111 → 2192 | Litho_FE_111 88, LithoTrack_FE_95 73 |
| 1.00 | slate | 3423 | 52.2 | 79.8 | 98.6 |  25 | 2056 → 2062 | WE_FE_41 139, Implant_128 133 |
| 1.05 | fifo  | 3399 | 50.4 | 80.6 | 96.8 |  99 | 2063 → 2244 | Litho_FE_111 58 |
| 1.05 | cr    | 3385 | 59.3 | 81.7 | 99.3 |   4 | 2117 → 2259 | Litho_FE_111 52, Implant_91 50 |
| 1.05 | slate | 3394 | 57.9 | 80.3 | 95.9 |  43 | 2058 → 2269 | LithoTrack_FE_115 96, Implant_91 77 |
| 1.10 | fifo  | 3388 | 49.0 | 80.2 | 93.2 | 223 | 2079 → 2424 | Litho_FE_111 224, WE_FE_41 101 |
| 1.10 | cr    | 3374 | 60.5 | 81.4 | 98.2 |   7 | 2133 → 2455 | LithoTrack_FE_115 160, Litho_BE_110 99 |
| 1.10 | slate | 3379 | 53.6 | 80.4 | 96.5 |  62 | 2063 → 2441 | Litho_FE_111 92 |
| 1.15 | fifo  | 3421 | 56.6 | 81.1 | 89.0 | 486 | 2081 → 2567 | Litho_FE_111 150, Litho_FE_98 93 |
| 1.15 | cr    | 3423 | 61.7 | 82.0 | 96.1 |  13 | 2138 → 2578 | Litho_FE_92 343, Litho_FE_111 113 |
| 1.15 | slate | 3464 | 55.7 | 81.3 | 96.6 |  70 | 2078 → 2506 | DE_FE_71 116, Implant_91 91 |
| 1.20 | fifo  | 3388 | 52.5 | 80.3 | 88.1 | 690 | 2087 → 2750 | Litho_FE_98 226, Litho_FE_111 185 |
| 1.20 | cr    | 3440 | 63.5 | 82.0 | 84.8 | 133 | 2141 → 2719 | Litho_FE_92 208, Litho_FE_111 193 |
| 1.20 | slate | 3424 | 55.7 | 81.0 | 91.9 | 145 | 2081 → 2706 | DE_FE_1 117, Litho_FE_111 105 |

## Reading

- **The knee is between 1.00 and 1.05, not 1.10.** At 1.05 every rule's WIP
  climbs ~9% over the window (starts ≈ 60/day against 57–59 completed); at
  1.10 and above WIP grows 3–9 lots/day without settling and the litho
  families (`Litho_FE_111`, `Litho_FE_92`, `LithoTrack_FE_115`) hold the
  queues, as the static load model predicted. The designed-load headroom
  (67% fab-wide) overstated the real one: PM, breakdowns and litho at 83–88%
  leave ~3–5% of start headroom.
- **Fab-wide utilization moves ~1.5 points across the whole grid** (80 → 82).
  Extra starts turn into queue at the bottleneck, not busy time elsewhere.
- **Under load, cr beats slate on both throughput and on-time.** At 1.05x cr
  completes 59/day at the end with 99.3% on-time; slate 58/day at 95.9%,
  below its own 98.6% at 1.00x. The slate's due-date pressure is capped
  (×3 at critical ratio ≤ 1) and its ageing term is weak; cr's ordering is
  the steeper one once queues form. Tuning target: a due tier with cr's
  slope, then re-test at 1.05.
- **Candidate that raises both metrics without giving up on-time:** `cr` at
  1.03–1.05x starts (+~1 point utilization, +5 lots/day, 99% on-time), with
  the caveat that WIP still drifts at 1.05 and will erode on-time over a
  longer window. `bench/results/starts120/` (120 days, 1.00 / 1.03 / 1.05,
  cr and slate) is the confirmation.

## 120-day confirmation (`../starts120/`, slate = pre-ADR-0012 defaults)

| x    | rule  | lots/120d | thr/d @end | util % | on-time % | tardiness | WIP first5d → last5d | longest queues at end |
|------|-------|----------:|-----------:|-------:|----------:|----------:|---------------------:|-----------------------|
| 1.00 | cr    | 6776 | 52.4 | 81.0 | 99.5 |  10 | 2111 → 2124 | Dielectric_BE_28 74, LithoTrack_FE_115 66 |
| 1.03 | cr    | 6832 | 58.7 | 81.9 | 98.6 |  15 | 2125 → 2221 | Dielectric_FE_31 111, Dielectric_BE_27 78 |
| 1.05 | cr    | 6874 | 54.2 | 82.0 | 96.5 |  62 | 2117 → 2192 | Dielectric_FE_31 62, DE_FE_72 61 |
| 1.00 | slate | 6793 | 56.0 | 80.3 | 96.1 | 106 | 2052 → 2133 | Implant_91 109, Planar_FE_78 82 |
| 1.03 | slate | 6806 | 57.5 | 80.6 | 88.6 | 267 | 2054 → 2257 | LithoTrack_FE_96 151, Planar_FE_78 87 |
| 1.05 | slate | 6867 | 59.9 | 81.0 | 75.8 | 971 | 2060 → 2208 | Dielectric_FE_31 109, Implant_91 71 |

- Over 120 days, 5% more starts bought 1.4% more completions: with a 36-day
  cycle time most of the window still drains pre-existing WIP, and the
  extra starts sit in WIP (+4–5% at 1.03, cr) and at the end-of-window queue.
- **cr at 1.03× is the confirmed sweet spot so far**: +0.9 points utilization,
  +6 lots/day at the end of the window, 98.6% on-time, WIP drift ~+5% over
  120 days. At 1.05× cr gives up three points of on-time for no more
  utilization.
- **The pre-0012 slate degrades with time under load**: 96.1 → 88.6 → 75.8%
  on-time across the scales, 971 lot-days of tardiness at 1.05×. This is the
  capped due term and the score fallback compounding over a longer window;
  the rematch rows (`../rematch/`, cr fallback + 900 s horizon + uncapped due
  term) are the test of the fix.

## Rematch at 1.05× (`../rematch/`, slate with ADR 0012 defaults: cr fallback, 900 s horizon, uncapped due term)

| rule | lots/60d | thr/d @end | util % | on-time % | tardiness | cycle time | WIP first5d → last5d | coverage | wall |
|------|---------:|-----------:|-------:|----------:|----------:|-----------:|---------------------:|---------:|-----:|
| cr | 3385 | 59.3 | 81.7 | 99.4 |  4 | 37.5 | 2117 → 2259 | – | 38 min |
| slate, old | 3394 | 57.9 | 80.3 | 95.9 | 43 | 36.4 | 2058 → 2269 | 50% (loose) | 127 min |
| slate, 0012 | 3397 | 50.7 | 80.6 | 99.2 | 21 | 36.0 | 2061 → 2220 | 47.5% (strict) | 243 min |

- The rework closes the on-time gap at 1.05× (95.9 → 99.2%, against cr's
  99.4) and keeps the slate's cycle-time advantage (36.0 d vs cr 37.5).
  Tardiness halves but stays above cr's; utilization is a point below cr.
- Completions over the window are identical (3397 vs 3385); the trailing
  end-of-window rate is noisy at this length and should not be read as a
  gap. The 120-day rows at 1.00 and 1.03 (`../rematch/slate120_*`) are the
  settled comparison.
- Wall clock 1.9× the old slate: the horizon's event-queue rescan. The
  predictive queue (NEXT §1) is the fix.

## 120-day rematch (`../rematch/slate120_*`, slate with ADR 0012 defaults)

| x    | rule        | lots/120d | thr/d @end | util % | on-time % | tardiness | cycle time | WIP first5d → last5d | coverage | wall |
|------|-------------|----------:|-----------:|-------:|----------:|----------:|-----------:|---------------------:|---------:|-----:|
| 1.00 | cr          | 6776 | 52.4 | 81.0 | 99.5 |  10 | 37.6 | 2111 → 2124 | – | 29 min |
| 1.00 | slate, old  | 6793 | 56.0 | 80.3 | 96.1 | 106 | 36.5 | 2052 → 2133 | 48% | 232 min |
| 1.00 | slate, 0012 | **6895** | 59.3 | 80.6 | **99.5** |  26 | **35.9** | 2056 → **2032** | 47% | 400 min |
| 1.03 | cr          | 6832 | 58.7 | 81.9 | 98.6 |  15 | 38.2 | 2125 → 2221 | – | 31 min |
| 1.03 | slate, old  | 6806 | 57.5 | 80.6 | 88.6 | 267 | 37.2 | 2054 → 2257 | 50% | 245 min |
| 1.03 | slate, 0012 | **6902** | 59.8 | 81.1 | **99.0** |  19 | **36.6** | 2051 → 2152 | 49% | 409 min |

- The reworked slate now leads cr on every column but utilization at both
  scales: +1.8% completions at 1.00× and +1.0% at 1.03×, on-time equal or
  better, cycle time 1.6–1.7 days shorter, and at 1.00× the only row whose
  WIP falls over the window. cr's remaining edge is ~0.8 points of
  utilization, which is lots sitting on tools longer, not more output.
- **Operating point: slate (0012 defaults) at 1.03× starts** — +1.0%
  completions over cr at the same starts, +1.6% over the fab at 1.00×,
  99.0% on-time, WIP drift ~+5% over 120 days. 1.05× remains a 60-day
  result only.
- Wall clock 400 min per 120 days (1.7× the old slate): the predictive queue
  is the fix before longer horizons are run routinely.
