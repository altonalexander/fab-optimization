# Lab notes — the scrap band, and the decision nobody could make

**2026-09-16, afternoon.** Three things since
[this morning's entry](2026-09-16-the-boundary-is-at-eight.md): the queue-time
consequence was switched to the testbed's own semantics, the scale sweep was
re-run under it, and a question about why the fab cannot run near its
published windows turned into a missing control rather than a broken dataset.

Numbers: [`bench/results/scrapfirst/`](../../bench/results/scrapfirst/)
(`bench/tools/analyse_scrapfirst.py`), the scale 8 / scale 1 rows from
[`bench/results/cqtdiag/`](../../bench/results/cqtdiag/), and
`bench/tools/cqt_window_slack.py`.

---

## Scrap on the first violation is now the primary configuration

WSC 2020 §2.1 says a lot that misses its queue time is "likely" to have to be
scrapped. Every run before today reworked it to the entrance step up to three
times instead. The diagnostic grid (`cqtdiag`) put the two side by side:

- at **scale 8** the two policies are indistinguishable — violations are rare,
  so no earlier row there is distorted by the choice;
- at **scale 1** rework-then-scrap collapses (≈21 lots/day, WIP diverging)
  while scrap-on-first stays flow-stable at 14.5 lots/day and scraps 73–76 %
  of material. Rework adds load exactly where capacity is short; scrap removes
  it. The collapse in the earlier sweeps was our rework choice, not the fab.

Rework stays in the paper as a sensitivity arm. It is not unrealistic — a lot
that waited too long after a clean can often be re-cleaned — but the dataset
does not say which windows are recoverable, so applying it to all 264 was an
unstated assumption.

## The scrap band

`qt` rule, scrap-on-first, 90-day warm-up plus 90-day window, five seeds:

| scale | seeds stable | shipped lots/day | scrapped share | cycle time (d) |
|---|---|---|---|---|
| 8 | 4/5 | 56.6 | 0.9–2.4 % | 37.2 |
| **5** | **5/5** | **52** | **8–12 %** | **34** |
| 3 | 5/5 | 37 | 32–39 % | 28 |
| 2 | 5/5 | 25 | 54–59 % | 25 |
| 1 | 5/5 | 14.3 | 73–76 % | 22 |

Shipped plus scrapped is ≈57/day at every scale, matching releases, so the
counters conserve. The promote threshold (0.25 / 0.50 / 1.00) moves scrap by a
point at most, so it cannot be used to tilt the comparison. One scale-8 seed
drifted at +7/day, just over the +5 stability line.

Three readings:

1. **Under scrap-on-first the fab never runs away.** Too-tight windows become
   lost material, not a jammed fab.
2. **On-time is ≈99.8 % everywhere and means nothing here** — scrapped lots
   are never late. Cycle time *falls* as windows tighten for the same reason:
   the fab is emptier. At these scales the headline metrics are scrap share
   and good lots shipped.
3. **Scale 5 is the band we were looking for.** 92 % of unconstrained
   throughput, stable on every seed, and a sort key still loses about one lot
   in ten — ~530 lots per seed over 90 days. Proposed operating point: scale 5,
   promote threshold 0.50 (best worst-case WIP slope, +1.0/day).

## Why scale 1 cannot run: not the data

`cqt_window_slack.py` compares every published window with the fastest a lot
could cover it — intervening processing plus the dataset's mean transport,
zero queueing:

- **no window is infeasible**; the tightest leaves 1.46× the minimum;
- **181 of 264 windows (69 %) go straight from entrance to exit** with no step
  between, and the common lengths are 2 h (116) and 1 h (44);
- median window / minimum time is 16×.

So the data is sound. The windows are simply short compared with a typical
queue at a busy tool: a lot usually has about an hour to *start* its next
step, and in an 80 %-utilised fab that is roughly an average wait.

The dataset also cannot tell us which windows are hard scrap gates and which
are recoverable: its 52 rework-flagged steps are ordinary inspection rework
and none sits on a queue-time entrance.

## The missing control

Confirmed in the code: `greedy.get_lots_to_dispatch_by_machine` sorts a free
tool's queue and runs the head. It returns nothing only for a busy mask, an
unfillable batch or a min-run setup conflict. **No rule can decline to start a
lot**, and the slate cannot either (`slate_rule.py`: "No holds: a tool never
waits"). `qt` can rescue a lot already inside a window; nothing can stop a lot
from *entering* a window its exit tool cannot honour.

Real fabs run queue-time zones with exactly that gate. Its absence means every
comparison so far — sort key against sort key, and the slate against both —
was between policies that are all missing the lever that matters most at
tight windows. A solver beating a sort key there would be beating a strawman.

### What was built

**Hold-before-entry** (`Instance.hold_blocks`, filter in `greedy.py` beside
the mask filter):

- a lot waiting at an entrance step is held while the exit family's estimated
  wait — lots queued there ÷ the family's start rate over its last 64 starts —
  exceeds `CQT_HOLD_FRAC` × the scaled window;
- lots waiting to *start* a window are excluded from that queue, or a window
  whose entrance and exit share a family would hold itself shut;
- a held lot is released after `CQT_HOLD_MAX_H` (default 24 h) regardless;
- a tool left idle only by holds parks and is re-offered on every lot-free
  event (`wake_hold_waiters`), as with masks;
- it applies to every rule and to the slate; unset = off and every earlier row
  is bit-for-bit unchanged; the setting is in the checkpoint key
  (`sim_feed.hold_key`, `_hq050m24`).

Tests (`bench/tests/test_cqt_hold.py`, 4/4; the 10 mechanism tests still pass
with holds on and off): six windowed lots behind a six-hour backlog on their
exit tool are **all scrapped without a hold and all saved with one**, with no
lot stranded; a 1-hour cap against the same backlog releases them and they are
scrapped; the gate is inert without enforcement.

A 5-day cold smoke run at scale 1 runs clean at ~1.8× the wall time. It shows
no scrap effect (307 against 309), which is expected — a cold fill has no
start history for the gate and most early windows were opened before it could
act. It says nothing about the warmed fab.

## What this changes, and the next test

The hold is a **baseline**, not our contribution: the obvious rule a fab
engineer would add. The comparison that matters is now solver against the
best sort key *with* holds. If holds alone recover most of the scrap at scale
5, the solver's room shrinks and we say so; if the gate's crude single-family
estimate leaves scrap on the table — chained windows, intervening steps, the
exit queue growing after entry — that remaining room is the coupled decision
the solver is for.

Next: a warmed sweep of the hold at scales 5, 2 and 1, two gate settings,
all five seeds, before any solver replicate.
