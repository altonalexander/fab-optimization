# Lab notes — the rule decides whether the fab survives

**2026-09-13.** Plain-language record of the night after
[the first one](2026-09-12-does-the-solver-earn-its-place.md). The formal
decisions are in [ADR 0016](../adr/0016-queue-time-enforcement.md) and
[ADR 0017](../adr/0017-fab-conditions-analysis.md); the numbers are in
[`bench/results/cliff/`](../../bench/results/cliff/). This page is the story.

---

## Where we started

Yesterday ended badly for the solver. Three constraint classes, three
negatives: tool qualification is just a filter, reticles never bind, and the
plain fab has nothing to optimise. So the plan was to make the fab *harder*
until simple rules broke, then show the solver holding on where they couldn't.

The last realistic thing missing was **queue time**: some steps must reach the
next step within a few hours or the wafers are damaged. It matters because it
is the first thing that lets the fab **lose** work rather than just be slow.

## Building it, and the thing that was missing

Enforcement went in quickly and the windows bit hard. Then the fab started
destroying itself — work in progress climbing to three times normal while
**tool utilisation fell to 30%.**

That combination is the tell. An overloaded fab pins its bottleneck near 100%
and queues work in front of it. Falling utilisation with rising inventory
means work is being *blocked*, not added.

The cause: a lot that missed its window went back to redo the step — and
nothing stopped it doing that forever. **422 violations landed on twelve
lots**, one of which was reworked 83 times. Those lots never left. They piled
up on a handful of steps and starved the rest of the fab.

The fix is what a real fab does: after a few failed attempts you **scrap** the
material. Unbounded rework was the unrealistic part. What we had built was a
deadline with a *retry*; what queue time actually is, is a deadline with a
*loss*.

## Then the fab still wouldn't stabilise

With the loop bounded, we swept the two things the experiment was designed
around — how tight the windows are, and how many lots we start — looking for
the edge where simple rules break down.

Every single cell failed. Work in progress climbed in all of them. The
conclusion looked obvious and I wrote it down: the fab has no spare capacity
(we already knew removing 5% of machines collapses it), so rework is extra
work it simply cannot absorb.

**That was wrong**, and the way it was wrong is the most useful thing here.

## The rule we were missing

Alton asked a good question: neither of our two rules knows queue times exist.
One sorts by age, the other by how late a lot is against its due date — and
the windows are measured in *hours* while due dates are in *days*. A lot two
hours from spoiling waits its turn behind a lot that is merely old.

So we added a third rule, `qt`, that puts lots at risk of spoiling first.

It turned out the original simulator authors had written exactly this and
**left it commented out** — in all five of their rules. Reasonable, since
nothing enforced queue times back then. It had been sitting there dormant.

## The bug that made it look terrible

First version: sort by how little time is left. It destroyed the fab —
throughput down 55%, inventory exploding.

The measurement that explained it: on a running fab, **two thirds of the lots
holding an open window have already missed it**, some by more than ten days.
Sorting by "least time left" put the most hopelessly late lots at the *front*
of every queue. Those lots were going to be scrapped or redone no matter what.
The fab was spending its capacity on work already guaranteed to be thrown
away.

A missed deadline can't be un-missed. Fixed: only prioritise lots that can
*still make it*; lots that are already past it take their normal turn.

Worth noting it was invisible in short tests and catastrophic in realistic
ones, because a fab that has just started running has few late lots.

## The result

Same fab, same demand, same machines, same starting conditions — only the
rule differs. Over six months of simulated time, at full production rate:

| rule | good lots/day | on-time | scrapped/day | inventory |
|---|---:|---:|---:|---|
| oldest-first | 40.2 | 22.5% | 6.3 | **climbing** |
| most-late-first | 44.4 | 15.2% | 3.8 | **climbing** |
| **queue-time-first** | **57.5** | **81.7%** | **0.0** | **steady** |

Repeated on three independent random seeds. Identical pattern every time:
`qt` steady with zero scrap; the other two climbing, one of them at a
remarkably consistent +10 lots/day in all three.

**The fab is not short of capacity.** It runs fine at full rate with queue
times, rework and scrap — if the dispatcher protects the windows that can
still be saved. The instability we spent the night chasing was a *dispatching
failure*, not a capacity limit.

That is a bigger claim than anything we had before. Earlier work moved
efficiency by a few percent. This is the difference between a fab that runs
and one that buries itself.

## What it means for the solver

Mixed, and mostly not good. We added queue time specifically to give the
solver something worth optimising — and a five-line sort key takes it to zero
scrap and roughly one violation a day. That's three constraint classes in a
row where a simple rule was enough.

What's left is narrower but better-shaped. About **7% of lots are still late**,
and it's lopsided: some products run at 99% on time, others at 84%, and the
same product is worst on every seed. The late ones miss by **half a day to a
day and a half** on cycle times of three to eight weeks — they arrive just
behind, not hopelessly behind.

That's a *due-date allocation* problem: there is slack sitting in the products
that finish early, and a shortage in the ones that don't. Moving a little
service from one to the other is a decision about a *set*, which is exactly
what an assignment solver claims to do and a single sort key cannot. It is
the first genuinely well-shaped target this project has produced.

## Honest accounting

Four things I got wrong, all caught by a control or an invariant rather than
by a better number:

- **Read a diverging fab as a capacity limit.** Twice. Both times the giveaway
  was utilisation falling while inventory rose.
- **Blamed the wrong mechanism twice** for `qt`'s collapse — setup changeovers,
  then batch-breaking. Setup turned out to be under 1% of machine time, which
  could never have explained a 55% loss. I should have checked the size of the
  effect before proposing the cause.
- **Mixed up two accounting periods.** Violations counted from the start of
  time, production counted only over the measured window. It implied 8,325
  lots came out of a fab that started 5,100. Caught only because we check that
  lots in = lots out + scrapped + change in inventory.
- **Sized the solver's opportunity from one seed**, right after writing down
  "one seed" as the main caveat. Two more seeds halved it.

The pattern from yesterday's notes held again: **every wrong answer came from
a number that couldn't respond to the thing being changed**, and every fix was
a control or a conservation check, never a cleverer metric.

## What's next

1. Make the solver faster. It spends 60% of its time in the solver itself and
   only 13% talking to Python — the opposite of what everyone remembers — and
   it solves each machine group one after another when they're independent and
   could go at once. Worth about 2×.
2. Run the solver against `qt` at this operating point, aimed at the due-date
   imbalance rather than at queue time.
3. Tune `qt` first if it wins, because beating a weak baseline proves nothing.
