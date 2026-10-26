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

---

## 12.10 The symmetric comparison, 2026-09-14

§12.9 predicted the §12.8 margin was a lower bound, because the measured
`slate` rows carried the *untuned* `qt` on ~54% of their decisions while being
compared against the tuned rule. Re-run with both sides tuned:

| run | good/day | on-time | CT (d) | tardiness | viol/day | per-part spread |
|---|---:|---:|---:|---:|---:|---:|
| `qt` untuned | 57.5 | 81.66% | 38.4 | 1,925 | 1.4 | 32.9 |
| `qt` tuned (promote < 50%) | 57.4 | 89.60% | 38.3 | 524 | 1.1 | 15.8 |
| `slate`, untuned fallback | 57.4/57.7 | 92.89/93.02% | 36.9 | 114/122 | 1.4/1.7 | 14.4/16.3 |
| **`slate`, tuned fallback** | 57.3 | **96.10%** | 37.2 | **77** | 1.6 | **10.1** |

The prediction holds. `slate` improves from 92.95% to **96.10%** when its own
fallback is fixed, so the symmetric margin against the strongest baseline is
**+6.5 on-time points and 6.8× less tardiness**, not §12.8's +3.3 and 4.3×.

### And it partially restores §12.4

§12.4's set-rebalancing claim was withdrawn in §12.8 because a tuned `qt`
matched `slate`'s per-part spread — 15.8 against 14.4. With **both** sides
tuned, `slate` reaches **10.1 against `qt`'s 15.8**.

So the withdrawal was correct on the evidence then available, and the
symmetric comparison puts a weaker version of the claim back: the solver does
rebalance across products better than a ranking, but by about six points of
spread rather than by being uniquely able to do it at all. Both cells are
n=1 and should be held loosely.

### Why this is easy to get wrong

`slate` is a solver *plus* a fallback, so it contains the baseline. Any
comparison must tune both or neither; tuning one silently measures the
handicap. Nothing in a result file records which `qt` a `slate` row contained,
so the error is invisible after the fact — the only defence is to re-run both
sides whenever either changes.

**v0.2.0 was released with the §12.8 numbers.** They are not wrong, they are
conservative; §12.9 already said so. The figures here supersede them.

## 12.11 The replicate batch, 2026-09-15: the win is seed-dependent

§12.10 was one solver run against one rule run on one seed. Overnight
2026-09-14/15 we ran the solid version: every sort key on seeds 0–4 (one run
each — the sort keys are bit-deterministic, §12.11.3), and the symmetric
solver (tuned `qt` fallback) three times on seeds 0 and 1. Seed 2's three
replicates were started; one finished and the other two were **stopped by
decision on 2026-09-15** so the paper could be rewritten on what was in hand.
Seeds 3 and 4 have no solver runs.

### 12.11.1 Paired, per seed

| seed | `qt` tuned on-time | `slate` on-time (3 replicates) | tardiness `qt` → `slate` | verdict |
|---|---:|---:|---:|---|
| 0 | 89.60% | 96.10 / 91.21 / 95.61% | 524 → 77 / 174 / 89 | solver better on every replicate |
| 1 | 99.65% | 97.79 / 97.62 / 99.08% | 20 → 127 / 57 / 36 | rule better on every replicate |
| 2 | 98.47% | 97.41% (n=1) | 42 → 67 | rule better (n=1) |

Throughput, violations, scrap (zero) and stationarity are indistinguishable
on every seed. Cycle time is 0.4–1.1 days shorter under the solver everywhere.

So §12.10's "+6.5 points, 6.8× less tardiness" is replicate **a** of seed 0.
The seed-0 range is +1.6 to +6.5 points and 3.0–6.8× on tardiness, and the
win holds on all three replicates. On seed 1 the solver loses by 0.6–2.0
points on all three. The paper's headline is now the pair, not the number.

### 12.11.2 The seeds are not equally hard, and the untuned rule says which

| seed | `qt` untuned on-time | `qt` tuned on-time | `slate` mean |
|---|---:|---:|---:|
| 0 | 81.66% | 89.60% | 94.31% |
| 4 | 85.27% | 95.51% | not run |
| 1 | 92.70% | 99.65% | 98.16% |
| 2 | 93.95% | 98.47% | 97.41% (n=1) |
| 3 | 95.51% | 96.99% | not run |

Seeds differ only in their random stream (breakdowns, repair times, process
time spread, sampling/rework draws, tie-breaks), but the drawn 270-day
histories differ a lot in stress: a nine-fold range in untuned tardiness on
identical demand. WIP at day 90 does not predict it (all within 7%). The
untuned rule's on-time — available before any solver run — ranks them.
**We chose seeds 0/1/2 by number before this table existed**, and so
replicated the solver on one hard seed and two easy ones while the second
hard seed (4) has no solver runs. Seed 4 replicates are the next experiment.

### 12.11.3 Sort keys are bit-deterministic

`qt50_1.00.json` and `qt50_s0_repeat.json` are digit-identical with the same
fingerprint (`f39ba74ce011049e`) over the full 180-day window. One run per
sort key per seed is the complete measurement. The solver is not: its 5 ms
per-family budget is wall-clock, and the seed-0 replicates span 4.9 on-time
points — the honest error bar on every solver claim.

### 12.11.4 Coverage, split

Raw coverage (46%) counts decisions with nothing to decide. Instrumented on
the second wave (`decisions_forced/choice/choice_covered`, and a candidate-set
histogram), from seed 1 replicate c over 3.87M decisions:

| candidate set | share of all decisions | share made by solver |
|---|---:|---:|
| 1 lot | 12.6% | 34% |
| 1 lot, other tools idle | 31.4% | 12% |
| 2 lots | 5.5% | 45% |
| 3–5 lots | 11.7% | 58% |
| 6+ lots | 38.8% | 75% |
| **≥ 2 lots (a real choice)** | **56.0%** | **68.6%** |

44% of decisions have one candidate. Of the decisions with a choice the
solver makes 69%, rising to 75% where six or more lots wait; the fallback's
share is concentrated where the choice is small or absent. A 20-day seed-0
probe gives the same effective coverage (69.0%).

### 12.11.5 What this changes

- The paper (`docs/paper/`) is rewritten with results as the spine: five-seed
  viability, the paired table, the difficulty table, the coverage split, ADR
  0016 §8 in threats, and the calibration episode compressed to half a page
  with its tables in an appendix.
- The README headline now quotes the seed-0 replicate range and says the
  solver loses on an easy seed.
- ADR 0016 §8 (window opens at start, not completion, of the entrance step)
  applies to every row here; the fix and a full re-run come before the
  robustness sweep.

## 12.12 Every term of the objective, audited on a real fab (2026-09-15)

`bench/tools/audit_objective.py` loads a warmed checkpoint, takes every
waiting lot, and evaluates each term of the solver's cost with the rule's
own code — the audit §7.5 of the paper said had never been done. Seed 0,
`qt`-warmed, day 90, scale 10; 1,108 lots waiting in 41 families.

| term | where | distribution over waiting lots | active? |
|---|---|---|---|
| base `lot.priority` | Python | 10 for all but hot lots (20); max 20 | hot-lot flag only |
| due, gentle `1+max(0,2−cr)` | Python | cr min 1.44, p10 2.44, p50 2.67; term ≠ 1 on **0.7%** of lots | nearly inert |
| due, steep `min(50,1/cr)` if cr<1 | Python | **no waiting lot has cr < 1** at this instant | inert here |
| ageing `1+min(1,wait/7d)` | Python | p50 1.03, max 1.34 | weak, as designed |
| downstream `0.8–1.25` | Python | p10 1.07, p50 1.21; ≠ 1 on 99.9% | active |
| batch cohort `×1.1` | Python | on 17% of lots | weak |
| q-time boost `1+600/max(s,60)`, window-relative | C++ cost | on the 13.5% of lots with a live window: p50 **2.13×**, max 4.2× | active, as intended after §12.4 |
| q-time penalty `1+3600/max(s,60)` | C++ unassigned | same lots: p50 7.8×, max 19.9× | active |
| time cost `setup+process` | C++ | process p10 0.3 h, p50 1.1 h, p90 7.5 h; setup min is **0 h for every lot** (a matching tool is always free) | dominant |

**What moves the objective.** Variance decomposition of log(cost) *within
families* — the only comparison the solver ever makes — pooled over the 41
families:

| term | share of within-family variance |
|---|---:|
| time cost (process time) | 42.8% |
| q-time boost | 30.5% |
| hot-lot base priority | 14.0% |
| downstream congestion | 4.8% |
| due date, gentle | 4.1% |
| ageing | 3.8% |
| due date, steep; batch | 0.0% |

**Order agreement.** Over 30,991 within-family lot pairs, the solver's cost
order agrees with **shortest-processing-time order 90.9%** of the time and
with **critical-ratio order 63.0%**. At this operating point the objective
is, to first order, *shortest job first, with a queue-time override and a
hot-lot bump*; the due date barely enters until a lot is already late.

**Why this is the easy-seed mechanism.** §12.11.1's loss on seed 1 had the
signature of cycle time falling while thin lateness appeared on every
product. That is exactly a shortest-job-first objective: it buys flow at
the expense of the marginal lot, and the tuned `qt` — pure critical-ratio
order among unpromoted lots — cannot lose that trade because it never makes
it. On the hard seed enough lots sit near or below cr = 1 that the steep
term and the q-time boost dominate, and the assignment pays.

**Two findings about the term the paper corrected.**

1. The window-relative boost is live for windows opened after resume, but
   the seed 0/1/2 checkpoints (built 2026-09-13) predate `lot.cqt_window_s`
   (added 2026-09-14), so the ~290 windows open *at* day 90 report raw
   slack and are inert until they close (≤ 240 h). On the order of 0.1% of
   the windows in a 180-day run; it does not move a result, but a
   checkpoint should carry the field, and the key now changes with the
   definition anyway (§8 of 0016).
2. `BatchGroup::should_fire()` compares `min_qtime_slack_s` with
   `1.2 × process_s` in **seconds**, so the window-relative pseudo-seconds
   (0–600) would fire every partial batch at once — but that method is only
   called from `test_main.cpp`, never from the planner. Inert by absence,
   noted so nobody wires it in without converting.

**What to change, in order.** (a) The gentle due term is flat above cr = 2
and reaches only 2× at cr = 1; a critical-ratio *ordering* needs a term that
is monotone through the whole 1–3 band where the waiting lots actually sit.
(b) Process time in the numerator is the shortest-job-first bias; the
intended reading was "cost of occupying the tool", but within a family the
tools are identical, so it only ranks lots by job length. Dividing it out
(or capping its spread) is the single change most likely to close the
easy-seed gap without touching the hard-seed win. (c) Re-run this audit on
a *stressed* snapshot (mid-window on seed 0) before any of it, since day 90
is warm but not stressed. None of this has been run.

### 12.12.1 Objective v2, built and re-audited the same day

`--slate-objective v2` (`slate_rule.due_term`, `REF_PROCESS_S`): the due
term is `clamp(3/cr, 1, 3)` above cr = 1 with the steep term unchanged
below it, and the cost numerator carries a fixed 3,600 s reference instead
of the lot's own process time. v1 remains the default and is what every
published row used. Same snapshot, same script, both versions:

| | v1 | v2 |
|---|---:|---:|
| due term ≠ 1 | 0.7% of lots | 70.9% of lots (p50 1.13×, p90 1.23×, max 2.1×) |
| within-family variance: time cost | 42.8% | 0.0% |
| q-time boost | 30.5% | 42.1% |
| hot-lot base | 14.0% | 25.2% |
| due date | 4.1% | 14.2% |
| downstream / ageing | 4.8% / 3.8% | 9.6% / 8.9% |
| order agreement with shortest-job-first | **90.9%** | **48.1%** (chance) |
| order agreement with critical ratio | 63.0% | 66.3% |

The shortest-job-first bias is gone. The objective is now, in order, the
queue-time override, the hot-lot flag, the due date, congestion and age.
Critical-ratio agreement moved only three points because the q-time boost
(2–4×) and the hot-lot flag (2×) still outrank a due term whose p90 is
1.23×; whether that is right is the imitation-floor question (§3b of
NEXT.md), not something to tune by hand here. **No run has been made with
v2.** The first should be the seed-1 pair (tuned `qt` vs `slate v2`), on
the corrected window, since seed 1 is where v1 lost.
