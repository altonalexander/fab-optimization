# Lab notes — what we are actually testing

**2026-09-15.** Written while the sweep runs, deliberately *before* the
numbers land, so the predictions on this page can be wrong in public. The
previous entries are [the solver's place](2026-09-12-does-the-solver-earn-its-place.md)
and [the rule decides](2026-09-13-the-rule-decides.md). The formal records are
[ADR 0016](../adr/0016-queue-time-enforcement.md),
[ADR 0017](../adr/0017-fab-conditions-analysis.md) and the
[spec audit](../audit/smt2020-columns.md).

---

## Where the week went

The paper is on `main`, honest and finished, describing runs made with a
queue-time window that was **measured from the wrong instant**. That is not a
retraction — the error is the same for every policy, so the comparison stands
— but it means the fab we characterised is stricter than the one SMT2020
specifies.

Finding that led to a correctness campaign rather than more experiments, and
it kept paying out:

1. **The window opened at the start of the entrance step, not its completion.**
   Every window was short by that step's own processing time — a median 44% of
   a native window, and for the worst pair in ten, more than the entire window.
2. **A lot that missed its *final* window shipped as a completion.** Rework and
   scrap live inside a loop over remaining steps, which never runs on the last
   step; six of the ten routes end on an exit step. Found by a synthetic test,
   never by a run.
3. **The dataset is not transport-free**, which we had asserted in the paper
   and in my own notes twice. Every family-to-family move draws 5–10 minutes,
   about 32 hours per completed lot. What is free is the *resource*: no
   vehicle, no contention.

Then today, the counter re-derivation
([`bench/tools/rederive.py`](../../bench/tools/rederive.py)): rebuild
throughput, cycle time, on-time, tardiness, violations, reworks and scraps
from a raw per-lot history, never from the simulator's own bookkeeping, and
compare. Four configurations, every counter exact.

**No defect.** First clean pass of the week. Every disagreement along the way
was a bug in the checker, and the five of them are written up in the audit
because each is a genuine trap in this data — step order is not unique across
routes, the completion time recorded at dispatch is only a *prediction* that a
tool breakdown invalidates, and so on.

I want to be careful about how much comfort to take from that. We keep finding
problems because we keep looking in new places, not because the code is
decaying. The next new place will probably also yield something. What has
changed is that the queue-time mechanism is now pinned from three independent
directions — the spec, synthetic tests through the real event loop, and an
offline rebuild of every counter — and that is a meaningfully different
epistemic position from "the runs looked plausible."

## The hypothesis

The whole programme rests on one claim, and it is worth stating plainly
because everything we are running is an attempt to break it:

> **An assignment solver beats a sort key only to the extent that dispatch
> decisions are coupled.** A sort key ranks lots one at a time; it cannot
> represent "if this tool takes lot A, that tool must take B." Where no such
> interaction exists, the best sort key is not merely competitive — it is
> *correct*, and the solver is a slower way to get the same answer.

Everything we have found is consistent with that, including the negatives. Tool
qualification turned out to be a filter, not a coupling. Reticles never bind
because masks are plentiful. The plain fab had nothing to optimise. Queue time
was the first mechanism that genuinely couples decisions — because a window
opened on one tool is only saved by what a *different* tool does next — and it
is exactly where the solver first won.

That gives a sharp, falsifiable prediction: **the solver's margin should be a
monotone function of how tight the windows are.** Loosen them and the solver
should converge on the rule and then lose to it; tighten them toward the
dataset's own values and the margin should widen.

The observed easy-seed loss fits this and is the part I trust least, so it is
worth naming: on a seed where the tuned rule already reaches 99.65%, the solver
lands 0.6–2.0 points *below* it. The objective audit explains the mechanism —
the v1 cost function was effectively shortest-job-first (91% order agreement
with SPT), which buys cycle time by spending the due dates of marginal lots.
That is a defect in our objective, not evidence for the hypothesis, and v2
exists to fix it. But it is also a reminder that "the solver wins where
coupling is high" must not quietly become "the solver wins where we looked."

## What is running right now

The sweep: **five seeds × five queue-time scales × three sort keys**, on the
corrected window, each cell warmed 90 days under `qt` and measured over the
following 180 days. Scale is the multiplier on the dataset's own windows, so
**scale 1 is the fab SMT2020 actually specifies** and 10 is where we have been
operating.

| | what it answers |
|---|---|
| scales 10 → 5 → 3 → 2 → 1 | the lowest scale at which the fab is viable at all |
| all five seeds | whether "viable" is a property of the fab or of a lucky draw |
| fifo, cr, qt | whether the *rule* decides viability, as it did at scale 10 |

Sort keys only. They are bit-deterministic, so a cell is one run rather than a
distribution, and they are cheap enough to cover the grid. The solver comes
after, at whatever operating point this picks.

**Predictions, recorded now.**

- There is a scale below which **no** sort key holds the fab, and above which
  `qt` holds it comfortably. The interesting region is narrow, and I expect it
  between 2 and 5 rather than at 1.
- `qt`'s advantage over `cr` and `fifo` **widens** as scale falls, because a
  window-blind rule cannot see the thing that is now killing lots.
- The promote threshold that tunes `qt` is **scale-dependent** and the value
  tuned at scale 10 will be wrong lower down. We already know the corrected
  window alone moved untuned `qt`'s on-time from 95.1% to 69.4% at scale 1, for
  the counter-intuitive reason that far more windows are now live *and
  saveable*. Any comparison that skips re-tuning is measuring our neglect.

**Early and unfinished:** fifty minutes in, every cell is still on its first
rule (`fifo`), and those rows show WIP climbing with scrap in the thousands
even at scale 10. `fifo` is *supposed* to diverge here — that was the headline
result of the 13th — so this is not yet a verdict. But if `qt` shows the same
signature at scale 10, then the operating point moves and recalibration becomes
the main event rather than a follow-up.

## The rules of the game, set before the answers arrive

Two of these exist because I nearly got them wrong.

1. **The operating point is chosen across all five seeds, not on seed 0**, and
   by a stress criterion fixed in advance: the lowest scale at which `qt` holds
   WIP stationary over the final third with scrap below a stated bound, on
   *every* seed. Not the scale that makes the solver look best.
2. **No seed is dropped after seeing its result.** The temptation was real and
   explicit — the easy seeds are where the solver loses — and selecting on
   outcome would turn the whole exercise into a story about us. If the easy
   seeds are uninformative, the fix is a harder fab for all five, chosen by a
   criterion written down first.
3. **Solver replicates on every seed, including seed 4.** Seed 4 is the second
   hard draw and has no solver runs at all; replicating seeds 0, 1 and 2 was
   the wrong choice made for no recorded reason.
4. **The imitation floor gets measured.** If a sort key trained to imitate the
   solver's decisions recovers most of the margin, then the value is in the
   objective and not in solving an assignment — and that is the honest finding,
   not a disappointing one.

## What would falsify the hypothesis

- `qt`, properly re-tuned at each scale, tracks the solver all the way down to
  scale 1. Then coupling is not the mechanism, or the per-family formulation is
  too local to exploit it.
- The solver's margin fails to widen as windows tighten. Same conclusion.
- The margin survives only on seeds we chose after the fact. Then there is no
  result, and the paper says so.

The outcome I would most like to avoid is the one where the solver wins by a
margin that is real, replicated and *uninteresting* — a couple of on-time
points bought with five times the wall clock, on a fab configuration we had to
search for. That is still worth reporting. It is just a different paper, and it
should be written in that voice rather than this one.
