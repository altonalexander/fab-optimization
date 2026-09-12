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
