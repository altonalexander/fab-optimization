# 0016 — Queue-time enforcement: give the fab a way to lose work

**Status:** Proposed, 2026-09-12. Plan only; no code, no numbers. Successor to
ADR 0014, whose answer was that the real-time layer should be a sort key — and
the one candidate left that changes the *shape* of the problem rather than its
strength. Gated on [ADR 0015](0015-right-sizing-the-tool-set.md): a constraint
cannot propagate through slack.

---

## 1. Why this, after two negatives

ADR 0013 (tool qualification) and ADR 0014 (reticles) both came back negative,
and the post-mortem in
[the lab notes](../notes/2026-09-12-does-the-solver-earn-its-place.md) gives
four conditions a constraint has to satisfy before an assignment solver can
beat a sort key. Each failure missed one:

1. **It must couple decisions, not filter them.** Qualification filters: skip
   what does not fit, take the next. A sort key handles that perfectly, at any
   strength — which is why tightening the matrix from 0.80 to 0.65 measured
   the same thing twice.
2. **The coupled resource must be scarce enough that blocking propagates.**
   Reticles couple correctly and sat at 27–34% of a mask-day; a blocked lot
   was always replaceable. ADR 0015 addresses this for the fab as a whole.
3. **Bad decisions must be expensive.** This is the gap. Today the worst
   outcome is a late lot, and below the knee total tardiness is a few
   lot-days across 913 tools. **The fab has no way to lose work.** A
   dispatcher that cannot destroy anything is very hard to beat.
4. **The right choice must depend on information a per-machine rule cannot
   have** — it must be about a *set*, not an ordering. A sort key produces a
   total order on lots; if the optimal decision is expressible as "sort by X",
   a solver adds nothing.

Queue-time limits are the only remaining candidate that hits **3 and 4
together**, which is why they come before reticles-on-a-tight-fab or anything
new.

## 2. What a queue time is, and why it is a set problem

A lot that finishes a clean or etch step must reach the next step within some
window — say four hours — or the surface degrades and the lot needs rework, or
is scrapped. SMT2020 ships the CQT columns for this. PySCFabSim parses them
and **ignores them** (ADR 0008 §2: "Queue-time constraints are read and
ignored"), `Instance.dispatch` has the handling commented out, and
`slate_rule.py` pins `QTIME_INERT = 1e9` with a comment giving exactly the
right reason: *"Feeding a real slack would have the solver optimise against a
signal the environment never punishes."*

Now put that on a batch tool. Diffusion furnaces hold ~6 lots and run for
hours. You have four lots queued, two of which expire in twenty minutes:

- fire now at 4/6 fill and waste a third of a bottleneck tool's shift, or
- wait forty minutes for a full load and scrap two lots?

And whichever you choose changes what is optimal at the next furnace.

**There is no sort key that expresses this.** Sorting yields an order; this
asks which *subset* to commit, evaluated jointly against a hard deadline and a
capacity quantum. It is the first constraint in this series where the decision
is genuinely about a set. The C++ side already anticipates it:
`BatchTool::should_fire` reads `min_qtime_slack_s` to decide whether to fire a
partial batch, and `qtime_slack_s` already crosses the C ABI. **The solver
half is built and waiting for the simulator to start punishing violations.**

## 3. Sketch of the decision (to be filled in when this is built)

### 3.1 Enforcement in the simulator

`Instance.dispatch` / `free_up_lots` gain the q-time clock: when a lot leaves a
step that opens a window, record the deadline; when it is dispatched to the
closing step, check it. On violation, either scrap (the lot leaves the fab and
its remaining work is lost) or rework (it returns N steps and consumes
capacity again).

**Scrap or rework is the one design question worth settling first.** Scrap is
the harsher and cleaner loss function and makes the experiment sharper. Rework
is more common in practice and partly self-correcting — the lot comes back and
eats capacity rather than vanishing, so the cost lands on the fab rather than
on the order book. The two produce different experiments and the choice should
be deliberate, not defaulted.

### 3.2 Un-inert the solver term

Replace `QTIME_INERT` with the real slack. This is a one-line change whose
whole point is that it must happen *after* §3.1, not before: a solver
optimising against an unpunished signal is the failure the constant exists to
prevent.

### 3.3 The overlay question

SMT2020's CQT columns are data the dataset already carries, so enforcement is
arguably not an overlay at all — it is honouring the testbed rather than
augmenting it. But an **enforcement flag** is still needed, because the
pristine no-enforcement rows have to stay reproducible, and the checkpoint has
to be keyed by it: a fab warmed without scrap has different WIP from one
warmed with it, exactly as ADR 0013 §3.5 argues for the qualification matrix.

### 3.4 Metrics

Throughput and cycle time are not the headline here. The headline is **scrap
rate at a stable WIP**, with on-time as the service constraint. Following
ADR 0014 §3.5 and ADR 0015 §3.3: per-part and per-family, never fab-wide
alone, and WIP-slope admissibility checked **before** any effect is read.

### 3.5 Runs

On the trimmed fab from ADR 0015, at a start rate where WIP is stationary.
`fifo` as a floor, and the real comparison is **`cr` against `slate`** — ADR
0014's lesson is that beating `fifo` is easy and uninformative.

## 4. What this assumes

- **The CQT data is meaningful.** It ships with the dataset and has never been
  exercised; the windows may be generous enough that nothing violates, in
  which case this fails condition 2 and the windows themselves become the
  knob.
- **Scrap is affordable to model.** A scrapped lot leaving mid-route touches
  the lot books, the WIP accounting and the completion KPIs; the `done_lots`
  path assumes a lot finishes its route.
- **Batch × q-time is where the interaction lives.** A q-time on a single-lot
  tool is closer to a due date, which `cr` already handles well. The
  hold-or-fire tradeoff is the part a sort key cannot express, so the batch
  families are the experiment.

## 5. What would overturn it

If `cr` matches `slate` on a tight fab with enforced queue times and batch
tools, then the real-time layer is a sort key and the matter is settled: the
optimisation effort belongs in the segment scheduler (NEXT §3), and this line
of investigation is closed rather than continued. Three constraint classes
across ADR 0013, 0014 and 0016 — filtering, coupling, and deadline-with-loss —
would be a fair test of the hypothesis, not a series of near-misses.

That is a genuinely useful outcome and should be written up as one. The
alternative failure — that nothing ever violates a queue time — is a weaker
result and means the windows need tightening before the question has been
asked at all.

---

## 6. Measured, 2026-09-12: the reroute was an absorbing state

§4 anticipated that scrap would be the awkward part — "the `done_lots` path
assumes a lot finishes its route." It was awkward for the opposite reason to
the one expected. Scrap was not hard to model; **the absence of it** was the
defect, and it did not present as a modelling gap. It presented as a fab that
diverged.

### 6.1 What it looked like

A 90-day warm-up under enforcement reached WIP 6551 against a 2053 no-q-time
control, with fab-wide utilisation at **30.4%** against 80.5%, and
idle-tool-hours-with-eligible-WIP-waiting up from 335 to 1053 per day.

The reading that does not work is overload. An overloaded fab pins its
bottleneck near 100% and queues WIP in front of it; here the busiest family
was 91.5%, most were near zero, and tools sat idle while lots they were
qualified to run waited. **Utilisation falling while WIP climbs means work is
being blocked, not added.** That is the same signature as the ADR 0014 §3.3
defect — a resource test used as an eligibility filter — and it was again
mistaken for a capacity cliff on first reading.

### 6.2 What it was

A depth probe over 30 cold days at window scale 8: **422 violations fell on
twelve lots.** Six reworked twenty or more times, one reworked 83 times, and
the violations concentrated on six step orders out of the route.

A lot violated its window, rerouted to the step that opened it, failed the
window again on the way forward, and cycled. Nothing terminated the loop, so
those lots never left. WIP accumulated as trapped material, concentrated on a
handful of steps, and starved the rest of the fab — which is why utilisation
fell rather than rose.

The fab-wide counter could not show this. 2772 violations/day against 6500 WIP
is consistent with both a broad tax spread over most lots and an absorbing
state on a few, and those demand opposite responses. Only the **distribution**
separates them, and nothing recorded it.

### 6.3 The fix, and what it says about the model

`cqt_max_rework` (default 3): past the cap the lot is **scrapped** — removed
from `active_lots` and deliberately *not* appended to `done_lots`, so it
counts against throughput and never as an on-time completion. A scrapped lot
in `done_lots` would read as a completion and hide the loss entirely.

This is not a workaround. Unbounded rework is the unrealistic part: a real fab
does not rework material indefinitely, it scraps it, and that scrap is the
loss this ADR set out to introduce. §2 argued queue time is the one constraint
class that is a *deadline with loss* — and the loss was missing from the
implementation. What existed was a deadline with a retry.

Verified rather than assumed:

| arm, 40 cold days | tput | WIP | util | idle-qual | viol | scrap |
|---|---:|---:|---:|---:|---:|---:|
| no q-time (control) | 2380 | 2054 | 81.9 | 323 | 0 | 0 |
| scale 8, detection only | **2380** | **2054** | **81.9** | **323** | 300 | 0 |
| scale 8, loop closed | 1932 | 2502 | 81.6 | 330 | 734 | 0 |
| scale 4, loop closed, **uncapped** | 700 | 3734 | **44.4** | 787 | 14518 | — |
| scale 4, loop closed, **capped** | 1612 | 2070 | 61.8 | 604 | 5216 | 752 |

- **Detection is free.** Digit-identical to the control on every field, at 40
  days as well as 8. Enforcement has no side effects, so anything else
  observed is attributable to the loop rather than to the machinery.
- **Scale 8 is an honest tax.** Utilisation unchanged (81.6 vs 81.9),
  throughput down 19% — the same tools, equally busy, producing less because
  they redo work. That is what rework should look like.
- **The cap recovers scale 4** from util 44.4/tput 700 to 61.8/1612, with 752
  lots scrapped instead of cycling forever.
- **A no-q-time regression control reproduces the pre-cap run exactly**
  (2380 / 2054 / 81.9 / 91.68 on-time), so the cap has not leaked into the
  route's own rework path.

The cap folds into the checkpoint key: capped and uncapped warm-ups are
different fabs, so pre-cap checkpoints are orphaned rather than silently
reused. Non-q-time checkpoint names are unchanged.

### 6.4 What is still unsettled

**The cap value is a parameter, not a measurement.** Three is defensible and
is not calibrated against anything. It should be swept once an operating point
exists, because it sets the exchange rate between rework and scrap and
therefore how much a violation costs — which is exactly what the solver would
be optimising against.

