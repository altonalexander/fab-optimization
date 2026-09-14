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

## Making the solver faster, and the thing that fell out of it

Before running the solver in anger we made it faster. Everyone "knew" the
bottleneck was the cost of shuttling data between Python and C++. Measured, it
isn't: **60% of the time is inside the solver, 13% is the data shuttling.**
Removing all of the shuttling would buy 15%. That belief was true once — in the
first version, which sent every waiting lot across the boundary every cycle —
and a later fix moved the bottleneck without moving the folklore.

The real opportunity was sitting in a comment. The solver handles each machine
group separately, the groups are independent, and the code said so in as many
words — then solved them one after another. Doing them at once: **2.9× faster**,
a little better than predicted.

Then we tried to prove it still computed the same thing, and couldn't.

## The bar that turned out to be wrong

The plan was simple: the run produces a fingerprint, so run it before and
after and check the fingerprints match. They didn't. So the parallel version
was buggy — except the lot counts and on-time were identical, which is a
strange way for a bug to present.

Alton asked whether "close enough" might be the right standard instead.

The test that settles it: run the *unchanged* version twice and see if it
agrees with itself. It doesn't. **181 lots one time, 176 the next, from
identical inputs.**

The reason is that the solver is given 5 milliseconds to think, measured in
real time. How much thinking fits in 5ms depends on how busy the machine is,
so two identical runs explore different amounts and return different — equally
valid — answers. The code says it is configured for exact replayability, and
it fixes the random seed, but the deadline is wall-clock. The promise isn't
kept. (There is a setting that would fix it; we haven't changed it yet because
it would shift the effective budget mid-experiment.)

So bit-identical was never achievable, and reaching for it was convenience
rather than rigour: it's a one-run check with an unambiguous answer. The
honest test is **does the change differ from the original by more than the
original differs from itself?** Original: 169–181 lots. Parallel: 173. Inside
the range, on every metric. Passes.

The part that matters beyond this one change: **if runs vary, a single result
can't be read.** The big comparison coming up therefore gets run twice, so it
has an error bar rather than a decimal point. That is the same mistake as
"sized it from one seed," one level down — and we only noticed because the
question was asked out loud.

## Four hours blind

The big comparison run printed **nothing** between starting and finishing.
That turned out to matter more than it sounds.

It ran for four hours and I gave three different estimates of when it would
finish, each from a different multiplier, each wrong — because with no output
there was nothing to correct them against. Worse: if the run had been failing,
we would have paid the full four hours to find out.

The obvious fix is to print progress, which we added — too late for the run it
was written for, since restarting to gain visibility would have cost more than
the visibility was worth.

So we measured the pace *beside* it instead: a short instrumented run over the
same fab, cheap enough to finish quickly. Within four minutes it answered the
question that actually mattered, which was not "how long" but **"is the solver
doing anything at all?"**

It was: the solver decided **47%** of dispatches, scrap stayed at zero and
inventory stayed flat. A solver run where that coverage number is near zero is
measuring its fallback rather than the solver, and that is worth knowing at
minute four rather than hour four.

One piece of discipline held here too. The short run's early throughput looked
far better than the sort key's — and it isn't a fair comparison, for two
reasons: our progress figure is a running average while the reference is a
trailing-day rate, and the sort key happens to have a dip in exactly those
days. Comparing across that would have flattered the solver for no real
reason. The honest comparison waits for the same window on both.

**Lesson: instrument the long run before running it, not after.** A run that
cannot be observed cannot be estimated, cannot be aborted early, and cannot
be trusted to be measuring what you think it is.

## And then we ran it

The solver went up against the queue-time sort key at the operating point,
twice, aimed at the due-date imbalance. Six months of simulated time each.

It lost, on everything.

| | good lots/day | on-time | scrapped | inventory |
|---|---:|---:|---:|---|
| queue-time sort key | **57.5** | **81.7%** | 0 | **steady** |
| solver (run 1) | 48.1 | 24.9% | 0 | climbing |
| solver (run 2) | 48.5 | 24.2% | 0 | climbing |

Lateness, totalled: **1,925 lot-days for the sort key, 91,888 for the
solver.** The two solver runs agree with each other to within half a lot a
day, so this isn't a fluke of one run.

The target had been the imbalance between products — some finishing at 99% on
time, others at 84%. The solver didn't close that gap, it flattened
everything: nine of ten products dropped 53 to 81 percentage points.

It also costs **5.7× the wall clock** — and that's *after* the 2.9× speedup.

### The part that nearly fooled us

Take the *same* solver run and just read it over longer and longer stretches:

| first ... of the run | lots/day | on-time |
|---|---:|---:|
| 20 days | 56.4 | **82.0%** |
| 60 days | 50.2 | 39.3% |
| 120 days | 48.6 | 27.9% |
| 180 days | 48.1 | **23.7%** |

At twenty days the solver is within two lots a day and about one point of
on-time of the sort key — a result anyone would write up as "no real
difference, needs tuning". At six months it is nine lots a day and fifty-eight
points worse. The slide is steady, not noisy: a backlog building, and
inventory rising the whole way.

**A short check would not have screamed that something was wrong. It would
have looked fine.** That is worse than being obviously wrong, because nothing
prompts you to look harder.

(We also ran a separate quick version that read better still, and briefly took
that as evidence the solver was winning. It wasn't a fair comparison — it
differed from the real runs in two ways, not one — and the difference was
small enough to be ordinary run-to-run variation anyway. Worth recording
because we nearly built a conclusion on it.)

The lesson: **a window long enough to be convenient is not long enough to be
right** — and the dangerous case isn't the short run that points the wrong
way, it's the short run that looks acceptable while the thing that ruins it is
still building.

## The reversal (2026-09-14)

Alton pushed back on the conclusion above, and the pushback was right.

His argument: an optimizer should never *lose* to a simple rule, because it
could always just copy it. If it loses, we aren't optimizing the right thing.
So what are we actually solving for?

We went and looked at what the solver is told about queue time. It gets one
number per lot — how much time is left before the window lapses — and turns it
into a preference with this:

    boost = 1 + 600 / seconds_of_slack

That constant is **600 seconds**. Ten minutes. It was written for a fab whose
queue-time windows are measured in minutes.

**This fab's windows are 10 to 240 hours.**

So a lot with sixteen hours left — which is the typical at-risk lot here —
received a boost of **1.01×**. One percent. Meanwhile the due-date term in the
same expression reaches fifty times. The queue-time signal wasn't weak; it was
arithmetically absent. Every conclusion we'd drawn about "the solver can't
handle queue time" was drawn from a solver that had effectively never been
told about queue time.

(There are two such constants, 600 in one place and 3600 in another, and the
design says they're supposed to be the same expression. They aren't.)

The fix is one line: instead of raw seconds, tell the solver what **fraction**
of its window a lot has left. Then a lot near the end of a ten-hour window and
one near the end of a ten-day window are treated the same way — which is what
the constraint actually means. Nothing else changed.

### What happened

| | good lots/day | on-time | cycle time | total lateness |
|---|---:|---:|---:|---:|
| queue-time sort key | 57.5 | 81.7% | 38.4 d | 1,925 lot-days |
| solver, before | 48.1 | 24.9% | 48.8 d | 91,889 lot-days |
| **solver, after** | **57.4** | **92.9%** | **36.9 d** | **114 lot-days** |

Same throughput, eleven points better on-time, a day and a half quicker, and
**seventeen times less lateness**. Inventory flat, scrap still zero,
queue-time violations back down to the sort key's level.

### And it won the way it was supposed to

The target was the imbalance between products: some finishing at 99% on time,
others at 66%. A single sort key can only rank, so it can't easily take slack
from one product and give it to another. An assignment across a whole *set*
can. That was the claim the whole project rested on.

| product | sort key | solver |
|---|---:|---:|
| the worst two | 66.4% · 66.7% | **84.9% · 88.6%** |
| the best three | 98.6% · 99.3% · 99.3% | 99.3% · 98.7% · 99.3% |

**The spread narrowed from 33 points to 14** — every laggard lifted by twelve
to twenty-two points, and not one of the leaders sacrificed to do it. It found
slack the ranking couldn't reach, which is precisely the thing an assignment
solver is supposed to be able to do and had never yet demonstrated here.

It costs about **4.7× the wall clock**, after the speedup.

### What this does and doesn't mean

It does **not** retroactively rescue the earlier three negatives. Machine
qualification really is just a filter. Reticles really don't bind on this fab.
Those were different mechanisms and this doesn't touch them.

What it does is make them suspect. If one hardcoded constant being wrong by
two orders of magnitude was enough to turn a decisive loss into a decisive
win, then "we tested it and the solver lost" is a weaker statement than it
sounded. We found this by reading the code and working out what boost a
typical lot actually receives. Nobody had done that for the other constants
either.

It also nearly went unnoticed. The solver had been losing for a reason that
looked like a *finding* — the fab diverges, the solver can't cope — and we had
written it up that way, with numbers, replicated. It took someone refusing to
accept that an optimizer should lose.

## Where that leaves the project

Four kinds of constraint. Three losses and one win:

- machine qualification — a filter; sorting handles it
- reticles — never actually binding on this fab
- queue time — a five-line sort key takes scrap to zero
- **due-date balance — the solver wins, clearly, once its queue-time term is
  on the right scale**

So the bet the project rested on is **not** dead. It was nearly buried by a
wrong constant, and the burial had been written up with numbers and
replicates.

Two findings stand, and they are independent:

**The choice of simple rule decides whether the fab survives at all.** A
queue-time-aware rule holds the fab steady at full production with zero scrap;
the two standard rules bury it. That is replicated on three seeds and is the
larger result.

**On top of a rule that keeps the fab alive, the solver buys real balance.**
Seventeen times less lateness, and the gap between best and worst product
halved, without sacrificing anything. That is one run so far, with a second
confirming.

## What's next

1. Confirm on more seeds. One win, one confirming run, one seed.
2. **Audit the other constants the same way.** We found this one by asking
   what number a typical lot actually receives. There are several more in the
   same expression and nobody has checked any of them.
3. **Stop hand-picking these numbers.** Treat the objective's coefficients as
   something to be fitted against a full run rather than guessed. The catch is
   that a short evaluation can't be trusted — we proved that tonight — so each
   fit costs a real run, which makes it expensive rather than hard.
4. Tune the sort key too, so the solver faces a harder bar than a first draft.
5. Re-test the earlier negatives, now that "the solver lost" has been shown to
   be a statement about a constant at least once.
6. Give the solver a deterministic deadline, so runs replay. The code already
   claims this and doesn't do it.
