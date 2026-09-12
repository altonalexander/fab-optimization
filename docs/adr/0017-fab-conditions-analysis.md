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
