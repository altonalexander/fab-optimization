# 0017 — Fab conditions analysis: find the cliff, then operate just past it

**Status:** Proposed, 2026-09-12. Design only; the grid runs after
[ADR 0016](0016-queue-time-enforcement.md) is built, because the mechanism
that makes the cliff sharp does not exist yet.

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

