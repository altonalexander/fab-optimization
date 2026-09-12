# Lab notes — does the solver earn its place?

**2026-09-12.** Plain-language record of a long day's work. The formal
decisions are in [ADR 0013](../adr/0013-tool-dedication-overlay.md) and
[ADR 0014](../adr/0014-reticle-overlay.md); the numbers are in
[`bench/results/dedication/`](../../bench/results/dedication/README.md) and
[`bench/results/reticles/`](../../bench/results/reticles/README.md). This page
is the story, for whoever picks it up next — including us in six months.

---

## What we are trying to accomplish

The simulated fab has ~1,300 machines and a constant stream of lots. Something
has to decide, moment to moment, **which lot runs on which machine.** Two ways
to do it:

- **A simple rule.** Look at the lots waiting, take the most urgent. Instant.
- **A solver.** Consider all lots and all machines together and compute an
  optimal assignment. This is the CP-SAT "slate". About **14× the compute.**

The project rests on a bet: the solver should beat the simple rule. If it
does, build the real scheduler. If it doesn't, the solver is dead weight.

## Why it was stuck

[ADR 0012](../adr/0012-starts-knee-and-what-the-slate-optimises.md) already
found the solver doesn't beat the simple rule — but for a reason that sounded
fixable. In this fab **every machine in a group is identical**: same speed, no
restrictions on what it can run. So "which lot on which machine" collapses
into "which lot next". There is no assignment problem, and a solver that
assigns things has nothing to assign.

That isn't a fair test. It's a chess engine playing tic-tac-toe. So the job
was to make the fab hard enough for the question to mean something.

## Three attempts, each closing the last one's loophole

### 1. Make machines non-interchangeable (ADR 0013)

Say each product may only run on *some* machines in a group. Now the machine
choice matters.

**No difference** — and the reason is structural rather than a tuning
problem. A restriction like this only filters the candidate list: the simple
rule skips what doesn't fit and takes the next thing. There's no puzzle in it.
We confirmed this isn't about strength by trying harder versions; the
generator actually *refuses* to go much harder, because past a point it stops
removing flexibility and starts removing capacity.

One genuinely interesting side-finding: when the restriction is **uneven** —
some machines are workhorses, others specialists — the rules separate
dramatically (a dumb rule collapses from 97% to 62% on-time). So the fab *can*
be made to care about dispatching. But even there the **simple rule beat the
solver**. A hard filtering problem is still a filtering problem.

### 2. Add photomasks (ADR 0014)

Real fabs have physical masks — one per product per layer — and a mask can
only be in one machine at a time. This is a genuinely different *kind* of
constraint: two lots can now block **each other** even when machines are free.
A simple rule structurally cannot see that, because it scores each lot on its
own. The solver already had this constraint built in and had simply never been
given the data.

**No difference — and we found out why.** The masks almost never collided.
With 251 masks spread over 82 machines, each mask was in use only ~27% of the
time; when one was tied up, the machine just ran a different product. Nothing
ever waited.

### 3. Make the masks scarce (the sweep)

Ramp a couple of products up so their masks are in constant demand. Nine
settings, pushing mask usage from 27% to 81%, with total fab starts held
constant so we were changing *concentration* and not *load*.

**Now the masks cost real money — about 15–20% of output — but not for the
reason we expected, and the solver still didn't help.**

## What actually costs the fab money

Moving a mask between machines takes 15 minutes. **That turned out to be the
entire cost.** Two checks make it unambiguous:

- Set the move time to **zero** and the masks become invisible — the fab
  performs identically to having no masks at all.
- **Triple how contended the masks are** and the penalty *doesn't change*. If
  masks were blocking each other, squeezing them would hurt more. It didn't.

So it was never about masks blocking each other. It was travel time.

## The punchline

Travel time is partly avoidable: run several lots of the same product
back-to-back on one machine and the mask stays put, so you never pay the move.
There was real money on the table.

- The dumb rule (first-in-first-out) gave away **756 lots** over 90 days.
- A **simple priority rule recovered a third of that** — 239 lots.
- The **solver recovered nothing more: 0.09%**, which is noise, for 14× the
  compute.

## What this means

**Don't build the real-time solver.** On this fab a good simple rule is as
good as optimization, instant, and 14× cheaper. That's a valuable answer — it
saves months building something that wouldn't pay for itself.

Two real limits on that:

- **It is a statement about *this* fab.** The benchmark models high-variety,
  low-volume production — many products, none in huge quantity. That shape is
  exactly what keeps masks from ever colliding. The *other* benchmark
  scenario (few products, high volume) needs 169% of a mask's available time
  at one mask per layer, i.e. it's infeasible without buying duplicates —
  masks there run at 84–93% and genuinely would fight. **If the real fab looks
  more like that, this answer could flip**, and the machinery to test it now
  exists.
- **Optimization may still pay elsewhere** — just not in the split-second
  "which lot next" decision. The natural place is planning a few hours ahead
  over the bottleneck area, the "segment scheduler" in
  [NEXT.md §3](../NEXT.md). Today's result says nothing against it.

## The methodology warning — read this before trusting any future run

We got the answer **wrong three times** before getting it right, always the
same way: **measuring something that couldn't respond to what we were
changing.** Each time the numbers looked convincing.

1. **Inventory drain read as production.** The solver appeared to complete up
   to 13% more lots. It was emptying the warehouse, not producing more — the
   extra completions exactly equalled the drop in work-in-progress, to zero
   residual in six of eight cases.
2. **A fab-wide average read for a per-product effect.** A mask constraint on
   a high-variety fab *cannot* move a fab-wide number: when one product's mask
   blocks, the machine runs one of ~250 other things and stays busy. The cost
   lands entirely on the blocked product. Averaging over all products was
   guaranteed to show nothing.
3. **A fab that had run out of work read as a constraint working.** A ramp
   compressed a finite release schedule, so the fab ran dry mid-window. It
   showed a *huge* apparent mask effect — cycle time +20 days, tardiness up
   17,000 lot-days — and was comparing two differently-starved fabs.

The fix each time was **a control or an invariant, never a better number.**
So the final answer has a no-masks control at every single setting, and an
explicit "is this fab even stable?" check that runs *before* any effect is
read. That check caught the third mistake before it reached a conclusion,
which is the only reason the day ended with something trustworthy.

Two of those were real simulator defects, not just analysis errors, and both
produced plausible output rather than crashes:

- a **resource** modelled as an eligibility test silently drains the fab,
  because eligibility is cached into queue membership (ADR 0014 §3.3)
- `scale_starts` re-times a *finite* pre-built schedule, so any large ramp
  runs it dry part-way through (ADR 0014 §3.5)

## What was built along the way, and is worth keeping

Useful regardless of which dispatcher wins, which ADR 0013 §7 predicted:

- the **overlay mechanism** — restrictions and masks as data beside the
  testbed, never edited into it, hash-keyed so a run can't silently resume the
  wrong fab
- a **generator that refuses** to write a configuration that deletes capacity
  instead of flexibility
- **masks end to end** — simulator resource, both solver paths fed real data
- **better instrumentation**: machine-group-scoped utilization, per-product
  KPIs, and work-in-progress-slope admissibility — each added because a
  fab-wide average had already hidden an effect or invented one

## If you want to reopen this

In rough order of cost:

1. **Run it on the high-volume scenario.** Masks genuinely bind there. This is
   cheap now and is the single most likely way to overturn the conclusion.
2. **Queue-time limits** (ADR 0014 §5). Already parsed by the dataset, ignored
   by the simulator, and deliberately switched off in the solver. It is the
   only candidate that gives the fab a way to **lose work** — today the worst
   a bad decision can do is make a lot late. A dispatcher that can't lose
   anything is hard to beat and hard to justify.
3. **The segment scheduler** (NEXT §3), which this result doesn't touch.
