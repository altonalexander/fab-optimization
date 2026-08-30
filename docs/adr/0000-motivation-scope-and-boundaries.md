# 0000 — Motivation, scope, and what this project is not

**Status:** Accepted, 2026-08-30. This one is foundational: if it changes,
most of the others need re-reading.

---

## 1. What problem this exists for

A 300mm fab dispatches lots to tools continuously, under constraints that do
not fit in a person's head: re-entrant routes of 30–40 mask layers, setup
changeovers with minimum run lengths, batch furnaces that must fire between a
minimum and maximum load, reticles that can be in exactly one scanner at a
time, queue-time windows that spoil material when missed, and tools that go
down without asking.

The industry answer is a dispatcher: something that decides, for each free
tool, which lot goes next. The interesting engineering question is not *can we
write one* — it is **can we tell whether ours is any good**, and can it answer
fast enough to be used.

Those two questions are why this repository has the shape it has.

## 2. The two claims under test

**Claim 1 — the architecture.** A dispatcher can be split into a slow planner
and a fast path: solve an assignment every 30–60 seconds, publish an immutable
`Slate`, and let the real-time path answer move requests by lookup in ~200 ns
without ever calling a solver. Solver latency then stops being on the critical
path, and a tool going down mid-cycle is handled by a precomputed alternate
rather than a re-solve.

**Claim 2 — the formulation.** The assignment model — feasible (lot, tool)
pairs, tool capacity, batch firing bounds, reticle exclusivity, an objective
weighted by priority and q-time slack — produces better fab outcomes than the
dispatch rules a fab would otherwise use (FIFO, critical ratio).

Claim 1 is largely demonstrated. Claim 2 **is not**, and no number in this
repository should be read as though it were. See 0002.

## 3. Scope — what is in

- A C++ dispatcher: solver, planner, slate, single-writer state mirror.
- A four-zone deployment topology, enforced by network isolation rather than
  convention, because a system that can reach both the tools and a browser is
  a different risk proposition from one that cannot.
- Equipment protocol down to the wire where it is cheap to be real (SECS-II),
  and honestly stubbed where it is not (HSMS timers).
- A vendored discrete-event fab simulator as the source of realistic load and
  as the yardstick to be measured against.
- A read-only enterprise mirror and dashboard, for seeing what the system
  decided and why.
- A benchmark harness whose entire purpose is to make claim 2 falsifiable.

## 4. Scope — what is out, deliberately

**This is not a MES, and not an APC/R2R system.** It decides lot-to-tool
assignment. It does not own recipes, process control, yield, or WIP
accounting; it consumes those as inputs.

**This is not a scheduler.** The solver has no time index. It assigns; it does
not sequence. "Lot A before lot B on tool T" is not expressible in the current
model, and that is a known limit, not an oversight — see 0002, where it is the
most likely reason the dispatcher could lose to critical ratio on cycle time
despite winning per cycle.

**This is not a capacity planning tool.** Nothing here answers "how many
scanners should we buy". The horizon is minutes to hours.

**This is not a product.** There is no multi-fab support, no auth, no
migration story, no operator training material. The dashboard is read-only by
construction and will stay that way until the ranking it displays is
trustworthy — a control surface over an untrusted ranking just gives people a
faster way to route around it.

**The simulator is a yardstick, not a component.** `baselines/pyscfabsim/` is
vendored read-only at a pinned commit. It is what the dispatcher is measured
*against*; it is never something the dispatcher depends on at runtime.

## 5. The boundary that matters most

The repository holds two programs that would otherwise have no reason to share
a tree. They share one because of a single invariant:

> If the dispatcher and the simulator are fed different SMT2020 loads, every
> number comparing them is meaningless, and nothing in either program would
> tell you.

`data/smt2020/`, symlinked into the baseline, is the enforcement. That symlink
is load-bearing. Breaking it does not produce an error; it produces confident
wrong numbers, which is worse. Everything else here is negotiable; this is not.

## 6. How to know this framing is wrong

- **If claim 1 fails**: measured fast-path latency stops being sub-microsecond
  under realistic load, or slate staleness between cycles costs more than
  solving inline would. Both are measurable and neither has been measured
  under load.
- **If claim 2 fails**: the dispatcher, run inside the simulator on equal
  terms, loses to critical ratio on cycle time and on-time delivery. That is
  the experiment 0002 exists to run, and it has not been run.
- **If the scope is wrong**: the honest failure mode is that assignment
  without sequencing is simply not where fab performance is won. If the
  measured gap between FIFO and CR is larger than any gap between CR and this
  dispatcher, the interesting problem was elsewhere and this project answered
  a question that did not matter much.

That last one would not be a disaster. It would be a finding, and this
repository is built to produce findings rather than to defend a position.

## 7. Reading order

`README.md` for orientation and honest status · this ADR for why · 0001 for
which fab we are simulating · 0002 for the experiment that has not happened
yet · `dispatch/README.md` for the dispatcher's own measurements and the
corrections that have already been forced.
