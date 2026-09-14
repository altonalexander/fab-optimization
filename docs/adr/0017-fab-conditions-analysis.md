# 0017 — Fab conditions analysis: find the cliff, then operate just past it

**Status:** **In progress, 2026-09-12.** §1–§7 are the original design,
unedited. §8 records what the first attempt found: the pre-flight gate killed
the matrix before it ran, and the admissibility criterion §5 states in advance
turned out to be insufficient once scrap existed.

Companion to 0016 rather than a successor to anything: 0013–0014 asked *does
the solver help*, this asks *under what conditions could it*, and answers the
second question first.

---

## 1. Why a grid, and why now

Every experiment in ADR 0013 and ADR 0014 picked an operating point by
argument and then measured one cell. Each time the cell turned out to be the
wrong one, and each time that was only discoverable after spending the compute:

- 1.00×/1.03× starts, below the knee — nothing to optimise, every rule at 98%+
- a mix ramp at constant lot count — overloaded the fab through the back door
- `trim-82`, 5% fewer tools — took on-time from 98.2% to **1.37%**

The last one is the informative failure. It is *not* a bad fab because the
rules fail — that is the regime being hunted. It is a bad fab because **WIP
climbs 7.5 lots/day**, so cycle time grows without bound and "1.37% on-time"
is a property of where the 90-day window was cut, not of `cr`.

Those are two different failure regimes and only one is usable:

| regime | WIP | what on-time means |
|---|---|---|
| **degraded, stable** | flat | a real property of the rule |
| **diverging** | climbing | an artefact of window length |

A grid locates both boundaries at once instead of guessing a cell and finding
out afterwards.

## 2. The two axes

**X — fab load.** Starts scale, holding capacity fixed. Drives queueing, and
is the axis whose knee ADR 0012 already located at ~1.03–1.05× on the
untouched fab.

**Y — queue-time window tightness.** A multiplier on the 264 windows the
dataset already ships (1 h to 24 h; `cqt_for_step` / `cqt_time`). Loose
windows are never violated and the axis is inert; tight windows destroy work.

Deliberately *not* an axis: **tool count.** ADR 0015 established this fab has
no slack to remove — 5% fewer tools is enough to diverge — so capacity is a
cliff, not a dial. And **due-date tightness** is held fixed, because it moves
the metric rather than the physics; if on-time needs to be harder without
destabilising, that is the knob to reach for, separately and knowingly.

## 3. Why the cliff should be *sharp* rather than gradual

This is the part worth predicting in advance, and it is the reason the grid
belongs after 0016 rather than before.

Queue-time violations feed back into load:

```
tighter windows → more violations → more rework → more load
       ↑                                              │
       └──────── longer queues ←──────────────────────┘
```

Rework is not a fixed tax; it is **re-entrant demand on the same tools that
were already too slow**. So above some window tightness the loop gains, and
the transition from "occasional violation" to "the fab is eating itself"
should be abrupt rather than linear. That is exactly the shape a scheduling
problem needs: a region where the decision barely matters, an edge, and a
region where it matters enormously.

It is also testable, and should be tested rather than assumed: **run the grid
with rework-on-violation on and off.** If the cliff is materially sharper with
the loop closed, the mechanism is confirmed. If the two grids look the same,
rework is a flat tax and the feedback story is wrong — worth knowing before it
gets written into anything.

Today the loop cannot exist: rework is modelled but negligible (52 of 4,013
steps, 1–2%, all on `Litho_REG`), so there is nothing for violations to feed.

## 4. What each cell reports

Per cell, for `fifo` and `cr` (the cheap rules — ~1/10th of `slate`):

1. **WIP slope** over the final third of the window. This is the
   admissibility gate and it is read **first**; a diverging cell is reported
   as diverging and its other numbers are not interpreted. ADR 0014's screen
   was wrong three times for want of exactly this.
2. **On-time and cycle time**, per part as well as fab-wide (ADR 0014 §3.5).
3. **Rework/scrap rate** from q-time violations — the loss function 0016 adds,
   and the headline metric once it exists.
4. **The `fifo` − `cr` spread.** This is the cell's *rule sensitivity*: how
   much the dispatching decision is worth here at all.

## 5. The target operating point, defined before the data

**Maximise the `fifo` − `cr` spread, subject to WIP being stationary.**

That is the cell where the decision demonstrably matters most while the fab
remains a viable thing to run — and therefore the only place a solver has
both room to win and a meaningful metric to win on. `slate` runs there and
nowhere else, which is what keeps this affordable.

Stated as a rule so it cannot be moved after seeing the numbers:

- **admissible** — |WIP slope| small enough that end-of-window WIP is within
  a few percent of start
- **rule-sensitive** — `fifo` − `cr` on-time gap materially larger than the
  seed-to-seed noise
- **the target cell** — the most rule-sensitive admissible cell

If no cell is both, that is the answer: on this fab there is no operating
point where dispatching matters and the fab is stable, and the real-time layer
is a sort key by default rather than by contest.

## 6. What this assumes

- **The shipped q-time windows are meaningful.** 264 steps carry them, 1–24 h,
  against queues measured in hours — so they should bite. If they never
  violate even at the tight end of the axis, the windows themselves become the
  experiment rather than a parameter.
- **Two axes are enough.** Load and window tightness are not independent
  (rework couples them), which is the point, but it also means the grid
  measures a surface rather than two curves. Sampling has to be coarse first
  and refined around the edge, not uniform.
- **`fifo` − `cr` predicts where a solver could help.** This is the weakest
  assumption in the page. It is plausible — if two sort keys cannot be told
  apart, a third method almost certainly cannot either — but the converse does
  not follow, and ADR 0014 has already shown a configuration where `fifo` and
  `cr` were 23 points apart and `slate` still lost to `cr`. Rule sensitivity
  is a necessary condition being used as a search heuristic, not a promise.

## 7. What would overturn it

- **A flat surface.** If the grid shows no cliff on either axis, the fab
  cannot be pushed into a regime where dispatching matters, and the whole
  line of work closes with the ADR 0014 verdict standing.
- **No admissible rule-sensitive cell** — the cliff exists but every cell past
  it diverges. Then the interesting regime is unreachable on this testbed, and
  the honest move is a different fab (the HVLM scenario) rather than a
  different constraint.
- **Rework makes no difference to the cliff's shape** (§3), which would mean
  the feedback mechanism this page is built around is not real.

---

## 8. Revision, 2026-09-12: WIP stationarity is no longer sufficient

The first matrix launch was abandoned at its gate, and the reason changes a
criterion this page stated in advance.

### 8.1 The gate earned its cost

§1 argued a grid beats picking a cell by argument. In practice what saved the
compute was smaller than the grid: a **16-day, `fifo`-only, six-cell probe
reading only rates and states** — WIP slope and violations per day, not
on-time or throughput, which a short window cannot measure. It ran in about
25 minutes and reported all six cells diverging, so the 2.5-hour matrix was
never spent on them.

That is worth keeping as a pattern. A grid whose cells are expensive deserves
a cheap pre-flight whose only job is to falsify the axis, sampling the same
cells and reading only the quantities a short window can support.

It also caught something the design had not allowed for: the **warm-up itself**
had diverged (day-90 WIP 6551 against a 2053 control). Every cell inherited
that, so the apparent differences between scales were inherited state rather
than measured response. Admissibility has to be checked on the warm-up, not
only on the measurement window.

The cause was [ADR 0016 §6](0016-queue-time-enforcement.md) — the rework
reroute was unbounded and acted as an absorbing state. §3 of this page
predicted the loop would gain; it gained without limit, which is not a cliff
but a missing termination rule.

### 8.2 The criterion has to change

§5 defined admissibility as "|WIP slope| small enough that end-of-window WIP
is within a few percent of start." With scrap in the model that is no longer
sufficient, and the probe showed why in one line: at window scale 6 the fab
warmed to **WIP 1727 — below the 2053 control — on 3256 scrapped lots.**

Scrapping holds WIP down by destroying material. A fab can therefore satisfy
the stationarity gate *because* it is eating itself. Read alone, WIP slope
would have marked that cell as the healthiest in the grid.

So admissibility becomes a conservation statement rather than a level check:

```
releases  =  good lots out  +  scrapped  +  dWIP
```

- **stationary** — dWIP small over the final third, as before
- **and not by loss** — scrap rate reported beside it, never summarised away
- **the economic metric is good lots out**, which `throughput` already is:
  scrapped lots never enter `done_lots`

This is the same failure mode as every earlier one in this repo — a metric
that could not respond to the thing being changed — and the fix is again an
invariant rather than a better number.

### 8.3 What the second matrix samples, and why it is smaller

The axis moved. Cold range-finding put the usable band at 3–10; with the loop
bounded, the scrap boundary sits between scale 8 and 10 — at 10 and 12 nothing
scraps and utilisation is indistinguishable from control, at 8 and below scrap
appears. So the grid samples **{6, 8, 10, 12}** to straddle it.

The **load axis is dropped** for this pass. Queue time is plainly the live
axis, load doubles the cost, and §6 already warned that two coupled axes
measure a surface rather than two curves — better to locate the edge on one
axis first. Load returns only if a cell proves admissible and rule-sensitive.

Rework-off controls run at one scrapping scale (6) and one non-scrapping scale
(10), so §3's falsifier — does the loop change the cliff's shape — is testable
at both ends rather than only where it bites.

### 8.4 A note on what short windows can and cannot say

Two readings were discarded during this work, both for the same reason, and
both of them mine:

- **40-day cold throughput and on-time.** A cold window is mostly fill-up, so
  the re-range came out non-monotonic (scale 10 apparently better than 12).
  Utilisation, WIP level and scrap counts survive; the ordering does not.
- **2-day warmed utilisation.** Compared against a 90-day control it produced
  an impossible result — scale 7 at 81.8% against scale 8 at 61.1% — because
  over two days utilisation is dominated by the instantaneous state. The
  probe's only valid output was `wip_first`, a state rather than a rate, which
  is what it had been designed to read.

The general rule this repo keeps rediscovering: **decide which quantities a
window can support before reading any of them**, and write that down first.


---

## 9. Answered, 2026-09-13: the cliff is the rule, not the conditions

§1 set out to find *conditions* under which dispatching matters — a load, a
window tightness, a cell on a grid. Both axes were swept and neither produced
one. The answer was on an axis the design never contained: **whether the rule
protects queue-time windows at all.**

### 9.1 The measurement

One fab, warmed 90 days under `qt`, resumed by all three rules. 180-day
window, 1.00× starts, windows at scale 10, rework on, scrap cap 3. Every row
conserves: releases 57.2/day against a 56.6 control.

| rule | good/day | punctual/day | on-time | cycle time | scrap/day | violations/day | WIP 2199 → | slope by third |
|---|---:|---:|---:|---:|---:|---:|---:|---|
| `fifo` | 40.2 | 9.1 | 22.5% | 59.4 d | 6.3 | 60.0 | 4117 | +14.7 / +18.4 / −1.0 |
| `cr` | 44.4 | 6.7 | 15.2% | 48.9 d | 3.8 | 88.6 | 3822 | +6.2 / +10.6 / **+10.4** |
| **`qt`** | **57.5** | **46.9** | **81.7%** | **38.4 d** | **0.0** | **1.4** | **2145** | +1.5 / −1.2 / −1.1 |

`qt` wins every column simultaneously — throughput, punctuality, cycle time,
scrap, violations — and is the only rule that leaves the fab stationary.
Tardiness makes the gap plainest: **1,925 lot-days against `cr`'s 82,296 and
`fifo`'s 154,688**, a factor of 43 and 80.

### 9.2 What it means, and what it retracts

**This fab is not capacity-limited under queue-time enforcement.** It absorbs
enforcement, rework and scrap at *full* load, with zero scrapped material,
81.7% on-time and flat WIP — provided the dispatcher protects windows that can
still be saved.

Which retracts the reading recorded in §8.3 and stated during the matrix: that
rework is re-entrant demand exceeding what ~5% of slack can absorb, and that no
operating point exists at 0.85–1.00× load. That conclusion was drawn from
`fifo` and `cr` alone, and both of them diverge here for a reason that is not
capacity. `cr` is *still* diverging at +10.4 lots/day in its final third while
sitting on the same tools that `qt` holds flat.

The mechanism is a backlog of unrecoverable work. A missed window reworks the
lot; the rework competes with fresh work; more windows lapse. `cr` and `fifo`
never break the loop because neither can see a window. `qt` breaks it by
spending capacity only where it still buys something.

### 9.3 Consequence for the question this project exists to answer

The headline is not that a sort key beat two other sort keys. It is that
**the dispatching decision determines whether the fab is viable, not merely
how efficient it is.** ADR 0013 and 0014 moved percentages; this is the
difference between a fab that runs and one that buries itself.

For the solver, the news is mixed and mostly bad. `qt` reaches **zero scrap
and 1.4 violations/day** with a five-element sort tuple. The loss function
[ADR 0016](0016-queue-time-enforcement.md) introduced to give CP-SAT something
to optimise is, at this operating point, already optimised away by a sort key.
That is the third constraint class in a row to end this way.

What remains is narrower but real: **18.3% of lots are still late**, and the
lateness is concentrated — `part_9` at 66.4% and `part_6` at 66.7% against
`part_4` and `part_10` above 99%. A per-part gap of 33 points on a fab with no
scrap is a due-date problem, not a queue-time one, and it is the kind of
imbalance an assignment across a set could address where a single sort key
cannot. That is where `slate` should be pointed, and it is a materially
different target from the one §3 anticipated.

### 9.4 The honest caveats

- **One seed.** Every row here is LVHM seed 0. The effect is far larger than
  seed-to-seed noise has ever been in this repo, but it is one seed.
- **`qt` is not tuned.** Slack-ordered, ahead of setup, cap at "can it still
  be made". No threshold, no lookahead, no hold decision. A better q-time rule
  probably exists, which makes it a *stronger* baseline for `slate`, not a
  weaker one.
- **The window scale is 10.** Tighter windows were not re-tested with the
  corrected rule, and §8.3's claim that scales 6–8 are unusable was measured
  with rules that could not protect windows. That claim should be regarded as
  unproven rather than established.

---

## 10. Replicated, 2026-09-13: three seeds, and a correction to §9.3

§9.4 listed "one seed" as the first caveat. Seeds 1 and 2 were run identically
— each warmed 90 days under `qt`, each resumed by all three rules, 180-day
window at 1.00× starts.

| seed | rule | good/day | on-time | scrap/day | violations/day | WIP | final-third slope |
|---|---|---:|---:|---:|---:|---|---:|
| 0 | `fifo` | 40.2 | 22.5% | 6.3 | 60.0 | 2199→4117 | −0.95 |
| 0 | `cr` | 44.4 | 15.2% | 3.8 | 88.6 | 2199→3822 | **+10.38** |
| 0 | **`qt`** | 57.5 | 81.7% | 0.0 | 1.4 | 2199→2145 | −1.13 |
| 1 | `fifo` | 41.8 | 24.7% | 0.8 | 32.9 | 2130→4766 | **+15.40** |
| 1 | `cr` | 46.6 | 34.0% | 2.6 | 74.1 | 2130→3567 | **+10.51** |
| 1 | **`qt`** | 56.8 | 92.7% | 0.0 | 1.1 | 2130→2207 | +2.08 |
| 2 | `fifo` | 41.5 | 22.4% | 0.7 | 33.4 | 2239→4934 | **+15.93** |
| 2 | `cr` | 46.1 | 22.9% | 3.0 | 72.8 | 2239→3695 | **+10.17** |
| 2 | **`qt`** | 57.6 | 94.0% | 0.0 | 1.3 | 2239→2164 | +0.57 |

**The headline holds.** `qt` is stationary in all three (−1.13, +2.08, +0.57),
scraps nothing in all three, and holds violations near one a day. `cr` diverges
in all three at a strikingly consistent +10.2 to +10.5 lots/day. `fifo` diverges
in two and saturates at WIP 4117 in the third. The §9.2 claim — that the
dispatching rule decides whether this fab is viable — is replicated.

### 10.1 What this corrects

§9.3 sized the solver's remaining opportunity from seed 0's per-part spread:
`part_9` at 66.4% against `part_4` at 99.3%, a 33-point gap. **Seed 0 is the
pessimistic outlier.**

| seed | worst part | best part | spread | fab on-time |
|---|---:|---:|---:|---:|
| 0 | 66.4% | 99.3% | **32.9** | 81.7% |
| 1 | 83.6% | 99.7% | 16.1 | 92.7% |
| 2 | 84.5% | 99.7% | 15.3 | 94.0% |

The *structure* is reproducible — `part_9` is the worst part in all three seeds
and `part_4` among the best — but the *magnitude* is roughly half what seed 0
showed, and fab on-time at a typical seed is ~93%, not ~82%.

So the honest target for `slate` is **~7% of lots late with a 15-point per-part
spread**, not 18% and 33 points. That is still a real and well-shaped
opportunity — a due-date allocation problem, with slack-rich parts to borrow
from, on a fab with zero scrap — but it is a narrower one than §9.3 claimed,
and the claim was made from a single seed after that seed's own caveat had
been written down.

---

## 11. ~~The answer, 2026-09-13: `slate` loses to a sort key, decisively~~

> **FALSIFIED 2026-09-14 — see [§12](#12-the-actual-answer-2026-09-14-slate-wins-once-its-q-time-term-is-on-the-right-scale).**
> Everything below was measured against an objective whose queue-time term was
> arithmetically inert at this fab's scale. It is kept unedited, because the
> reasoning is sound given the numbers and the numbers were real — what was
> wrong was a constant nobody had checked, and that is the useful part of the
> record.

### 11.0 The original section follows

§10.1 named the target: ~7% of lots late with a 15-point per-part spread, on a
fab with zero scrap and slack sitting in the products that finish early. A
due-date **allocation** problem — the one shape a decision about a *set* should
beat a ranking on.

It does not. Two replicates, one `qt`-warmed fab, 180-day window, 1.00× starts,
q-time term live, `qt` as the fallback for the ~47% of decisions the solver
does not cover:

| | good/day | on-time | cycle time | violations/day | WIP 2199 → | final-third slope | wall |
|---|---:|---:|---:|---:|---:|---:|---:|
| **`qt`** | **57.5** | **81.7%** | **38.4 d** | **1.4** | **2145** | **−1.13** | **3,113 s** |
| `slate` a | 48.1 | 24.9% | 48.8 d | 22.2 | 3843 | **+10.40** | 17,776 s |
| `slate` b | 48.5 | 24.2% | 48.6 d | 23.1 | 3765 | **+8.95** | 17,626 s |

Tardiness: **1,925** lot-days against **91,888** and **91,583**.

All three pre-registered bars fail. WIP diverges rather than holding
stationary; throughput is 16% below `qt`; and the per-part spread did not
close but collapsed — `part_9` 66.4% → 11.7%, `part_10` 99.3% → 18.1%, every
product but `part_7` down 53 to 81 points. The replicates agree to within
0.4 lots/day and 0.7 on-time points, far inside the noise floor measured
in §11.2, so this is not a seed or a scheduling coincidence.

### 11.1 Why, as far as the data says

`slate`'s divergence signature is nearly identical to `cr`'s from §9.1:
+10.38 lots/day and WIP → 3822 there, +10.40 and → 3843 here. **With the
q-time term live and a `qt` fallback on half its decisions, the solver still
behaves like the rule that diverges.**

The likely reason is instrument, not information. `qt` expresses queue time as
a **hard lexicographic tier** — an at-risk-but-saveable lot outranks
everything else, full stop. The solver expresses it as a **soft multiplier**
inside a cost ratio, `qtime_boost = 1 + 600/max(slack, 60)`, which competes
with setup time, processing time and urgency rather than dominating them. At
22 violations/day against `qt`'s 1.4, the term is plainly too weak to break
the backlog loop, and once the backlog builds everything else follows.

That is a fixable-sounding diagnosis and it should be treated with suspicion,
because ADR 0012 had one too ("every machine in a group is identical") and
fixing it did not help. A stronger q-time weight is a tuning exercise on a
method that has now lost on four constraint classes.

### 11.2 The short window looked acceptable

Read the SAME run over growing windows — one configuration, no confounds:

| `slate` a, window | throughput | on-time | WIP |
|---|---:|---:|---:|
| days 90–110 | 56.4/d | **82.0%** | 2181 |
| days 90–150 | 50.2/d | 39.3% | 2351 |
| days 90–210 | 48.6/d | 27.9% | 2643 |
| days 90–270 (full) | 48.1/d | **23.7%** | 2942 |
| *`qt`, days 90–110* | *58.5/d* | *83.3%* | *2205* |

**At 20 days `slate` is within 2 lots/day and 1.3 on-time points of `qt`** — a
result anyone would report as "no material difference". At 180 days it is 9
lots/day and 58 points worse. The decay is monotonic, so it is not noise: it
is a backlog compounding, and WIP climbs the whole way.

A 20-day probe run separately at `--threads -4` read better still — 58.1/day
and 87.4% on-time, which would have read as `slate` *winning*. That probe
differed from the long runs in two ways, window AND worker count, so its
margin cannot be attributed to the window alone. The gap between it and the
long run's own early window (58.1 vs 56.4, ~3%) also sits inside the
serial-vs-serial noise floor measured at 169–181 lots, so a thread effect and
ordinary variance are indistinguishable at n=1 each. Recorded because the
first draft of this section claimed the probe proved the short window
misleading, which over-read a confounded comparison.

The lesson does not need that claim. **A window long enough to be convenient
is not long enough to be right** — and the failure mode here is not a short
window pointing the wrong way, which would be obvious, but a short window
looking *acceptable* while the mechanism that ruins the run is still building.

### 11.3 What it costs

**5.7× the wall clock** (17,776 s against 3,113 s) *with* the family-level
parallelism added in `dd65189`; roughly 17× the CPU without it. The "14×"
quoted through ADR 0014 and 0016 came from a different configuration and
should not be repeated; 5.7× wall / ~17× CPU is the measured like-for-like
figure at this operating point.

### 11.4 What this settles

Four constraint classes, four negatives:

| | class | outcome |
|---|---|---|
| [0013](0013-tool-dedication-overlay.md) | unary / filtering | qualification is a filter; a sort key handles it |
| [0014](0014-reticle-overlay.md) | coupling | reticles never bind on this fab |
| [0016](0016-queue-time-enforcement.md) | deadline-with-loss | a five-element sort tuple takes scrap to zero |
| **here** | **due-date allocation** | **the solver makes it dramatically worse** |

ADR 0012's overturn condition — "an overlay fab where the assignment solver
still adds nothing over a sort → drop CP-SAT from the real-time layer and keep
it for the segment schedule only" — is met for the fourth time, and this time
on the class that was the best remaining candidate rather than a near-miss.

**The recommendation is to close the real-time CP-SAT line.** The dispatching
decision on this fab is a sort key; the useful finding of the work is §9's,
that *which* sort key decides whether the fab is viable at all.

### 11.5 What would overturn this

- **A stronger q-time weight in the cost function** closing the gap. Worth one
  attempt (§11.1), and it is tuning, not a new claim.
- **`qt` tuned into a harder baseline** that `slate` then beats — unlikely to
  reverse a 33-point on-time gap, but it is the honest version of the test.
- **A fab with a binding coupling constraint.** Every negative so far is on
  LVHM, where reticles never bind and tools within a family are identical. The
  HVLM scenario is the remaining place the assignment formulation could earn
  its cost, and nothing here speaks to it.

---

## 12. The actual answer, 2026-09-14: `slate` wins, once its q-time term is on the right scale

§11 is wrong, and the way it is wrong is worth more than the conclusion it
reached.

### 12.1 What was actually measured in §11

Both C++ queue-time terms are hardcoded in **minutes**:

```cpp
cost()            qtime_boost = 1.0 + 600.0  / max(lot.qtime_slack_s, 60.0)
CP-SAT objective  urgency    *= 1.0 + 3600.0 / max(m.lot_slack_s[l],  60.0)
```

This fab's windows are **10 to 240 hours** — the dataset's 1–24 h at
`--cqt-scale 10`. The measured p75 of *saveable* at-risk lots is **+16 hours**
of slack, which those expressions price at **1.010×** and **1.063×** — against
a due-date term in the same product that reaches **50×**.

The term was not weak. It was arithmetically absent. So §11 did not measure a
solver that handles queue time badly; it measured a solver that had, in
effect, never been told about queue time. `1aa5428` replaced the `1e9`
sentinel with real slack and changed nothing, for this reason.

(The two constants also disagree with each other, where
[ADR 0009](0009-slate-rule-hybrid-split.md) says the CP-SAT objective should be
"the linearized form of `SolverExporter::cost`". Left as found and flagged.)

### 12.2 The fix

One line of data, no C++ change: pass `600 * (slack / window)` rather than raw
slack, making both expressions **window-relative** — `cost()` becomes
`1 + 1/frac`, the CP-SAT objective `1 + 6/frac`. A lot near the end of a
10-hour window and one near the end of a 240-hour window are then treated
alike, which is what the constraint means. Lapsed windows still report inert
(§11.1's other lesson). `instance.py` stores `cqt_window_s` so the length is
available at the point of use.

### 12.3 The result, two replicates

| run | good/day | on-time | CT (d) | scrap/day | tardiness | viol/day | end WIP | final 3rd | wall |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| `qt` | 57.5 | 81.66% | 38.4 | 0.0 | 1,925 | 1.4 | 2145 | −1.13 | 3,113 s |
| `slate` §11 a | 48.1 | 24.91% | 48.8 | 0.0 | 91,889 | 22.2 | 3843 | +10.40 | 17,776 s |
| `slate` §11 b | 48.5 | 24.17% | 48.6 | 0.0 | 91,583 | 23.1 | 3765 | +8.95 | 17,626 s |
| **`slate` fixed a** | **57.4** | **92.89%** | **36.9** | 0.0 | **114** | 1.4 | 2154 | +0.43 | 14,739 s |
| **`slate` fixed b** | **57.7** | **93.02%** | **36.9** | 0.0 | **122** | 1.7 | 2106 | +0.68 | 14,781 s |

The replicates agree to **0.13 on-time points** and an identical cycle time —
far inside the noise floor of §11.2 — so the reversal is not a run-to-run
artefact. Conservation holds at 57.2 releases/day.

Against `qt`: **+11.3 on-time points, 16× less tardiness, 1.5 days shorter
cycle time**, at the same throughput, the same violation rate, the same zero
scrap, and stationary WIP.

### 12.4 It wins by rebalancing a set, which is the claim

§10.1 named the target as a due-date **allocation** problem, and that is
exactly where the gain appears:

| part | `qt` | `slate` §11 | `slate` fixed | Δ vs `qt` |
|---|---:|---:|---:|---:|
| part_9 | 66.41% | 11.67% | **84.88%** | **+18.5** |
| part_6 | 66.73% | 13.64% | **88.57%** | **+21.8** |
| part_3 | 77.38% | 14.75% | 93.15% | +15.8 |
| part_2 | 78.03% | 15.24% | 93.80% | +15.8 |
| part_1 | 76.97% | 14.10% | 92.34% | +15.4 |
| part_7 | 98.55% | 99.43% | 99.32% | +0.8 |
| part_10 | 99.32% | 18.07% | 98.74% | −0.6 |
| part_4 | 99.33% | 19.62% | 99.32% | −0.0 |

**Per-part spread falls from 32.9 points to 14.4.** Every laggard rises 12–22
points and not one leader is sacrificed — `part_4`, `part_7` and `part_10` all
hold at 99%.

> **CORRECTION, later the same day (§12.8).** The sentence that stood here —
> that a sort key cannot move slack between products and this was therefore
> the ADR 0009 set-assignment claim demonstrated at last — is **wrong**. A
> `qt` with a promotion threshold reaches a spread of 15.8, statistically
> indistinguishable from `slate`'s. The rebalancing was not a property of
> assignment over a set; it was a property of not promoting lots that were
> never in danger, and one threshold buys it. The margin that survives is
> real but smaller and differently shaped — see §12.8.

### 12.5 What this does and does not overturn

**Does not:** ADR 0013 (qualification is a filter) and ADR 0014 (reticles
never bind on LVHM) rest on different mechanisms and are untouched. ADR 0016's
finding that a sort key takes scrap to zero also stands — `slate` matches it
rather than beating it.

**Does:** §11's verdict, and the "four negatives" framing built on it. It also
weakens the standing of the other negatives *as evidence*, without
contradicting them: if one unchecked constant, wrong by two orders of
magnitude relative to the data, could inverse a result that had been measured,
replicated and written up, then "we tested it and the solver lost" is a weaker
claim than it reads. Nobody has audited the remaining coefficients the same
way.

**§9 is untouched and remains the larger result**: which sort key you run
decides whether the fab is viable at all, replicated on three seeds. `slate`'s
win sits *on top of* a rule that keeps the fab alive — it is warmed from, and
falls back to, `qt`.

### 12.6 Cost

**4.7× the wall clock** against `qt` (14,739 s vs 3,113 s) with the
family-parallel path of `dd65189`. Coverage is ~46%, so roughly half the
decisions are still the `qt` fallback.

### 12.7 What would overturn *this*

- **A tuned `qt`.** The baseline is a first draft: it promotes *any* saveable
  at-risk lot, whether it has twenty minutes or two hundred hours of slack —
  the same species of unchosen parameter this section is about. A `qt` that
  promotes only lots below a fraction of their window may close some of the
  gap. Running at the time of writing.
- **Other seeds.** One seed (0), two replicates.
- **The audit.** If the remaining objective constants are as miscalibrated as
  this one was, the tuned result could move again in either direction.

---

## 12.8 Calibrating against a tuned baseline, and what the win actually is

§12.7 named a tuned `qt` as the live overturn condition, on the grounds that
the published baseline promoted *any* saveable at-risk lot — twenty minutes or
two hundred hours of slack alike — which is the same species of unchosen
parameter as the constant §12.1 is about. It was run.

| run | good/day | on-time | CT (d) | tardiness | viol/day | per-part spread | wall |
|---|---:|---:|---:|---:|---:|---:|---:|
| `qt` promote-all (as published) | 57.5 | 81.66% | 38.4 | 1,925 | 1.4 | 32.9 | 3,113 s |
| **`qt` promote < 50% of window** | 57.4 | **89.60%** | 38.3 | **524** | 1.1 | **15.8** | 3,067 s |
| `qt` promote < 25% of window | 57.2 | 88.82% | 38.4 | 809 | 1.4 | 16.0 | 3,045 s |
| `slate` fixed a | 57.4 | 92.89% | **36.9** | **114** | 1.4 | 14.4 | 14,739 s |
| `slate` fixed b | 57.7 | 93.02% | **36.9** | **122** | 1.7 | 16.3 | 14,781 s |

### What this removes

**The mechanism claim.** §12.4 attributed `slate`'s win to rebalancing across
a set — something a ranking supposedly could not do. A thresholded `qt` gets
the same spread. So the per-part rebalancing is available to a sort key, and
the ADR 0009 claim remains **undemonstrated** rather than proven. That was the
most interesting sentence in §12 and it does not survive.

**Most of the magnitude.** The on-time margin against the best `qt` is
**+3.3 points**, not the +11.3 reported against the untuned one.

### What remains

Against the strongest baseline we have:

- **on-time 92.95% vs 89.60%** — +3.3 points
- **tardiness 118 vs 524 lot-days** — 4.3× less
- **cycle time 36.9 d vs 38.3 d** — 1.4 days shorter
- matched on throughput, violations, scrap and WIP stationarity

at **4.8× the wall clock**.

Tardiness is the most interesting of these, because it is the only one where
the gap stayed large after the baseline was tuned: `slate` is not just missing
fewer dates, it is missing them by far less when it misses. That is a
different claim from §12.4's and it has not been explained.

### The honest asymmetry

`slate` has two replicates agreeing to 0.13 on-time points. Each `qt` variant
has **one run**. A two-replicate mean against a single run is not a balanced
comparison, and the 3.3-point margin should be read with that in mind. The
replicates were not run because the object of this work is a **minimum viable
solver**, not a fully characterised sort key — but the asymmetry is recorded
rather than left for a reader to notice.

### Verdict

**A minimum viable solver is demonstrated.** On a fab with enforced queue
times, rework and scrap, at full load, `slate` matches the best sort key on
every stability and volume measure and beats it on lateness — decisively on
tardiness, modestly on on-time — for roughly five times the compute.

That is a narrower result than §12.4 claimed and a real one. The route to it
is the more useful record: the solver spent four constraint classes losing,
and the last of those losses was caused by a constant off by two orders of
magnitude relative to the data it was applied to.

### 12.9 The comparison in §12.8 is unfair to `slate`, and understates it

Coverage is ~46%, so **the `qt` fallback decides more than half of a `slate`
run**. The `slate` rows in §12.8 were produced before the promotion threshold
existed, so they ran the *untuned* sort key on that majority of decisions —
while being measured against the *tuned* one.

Both halves of `slate` should improve when the baseline does, because one of
those halves **is** the baseline. So the +3.3 on-time points and 4.3× tardiness
margin in §12.8 are a **lower bound** on the tuned-against-tuned comparison,
not an estimate of it.

This is a general property of the design rather than an oversight in one run:
`slate` is a solver *plus* a fallback rule, so every improvement to the rule
raises the solver's floor too. Any future baseline change requires re-running
`slate` against the same baseline, or the comparison silently favours whichever
side was tuned last. Recorded here because the failure mode is invisible in the
numbers — both rows look complete and neither reports which `qt` it contained.

A `slate` run with the tuned fallback is in flight; §12.8's verdict stands as
the conservative version in the meantime.
