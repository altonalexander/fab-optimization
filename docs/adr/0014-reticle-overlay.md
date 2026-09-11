# 0014 — Reticles: the coupling constraint, and the loss function the fab lacks

**Status:** Implemented 2026-09-11, first rows pending. Successor to ADR 0013,
whose qualification matrix did not separate the rules. Written for hand-off:
the mechanism, the generator and both solver paths are in place; what remains
is the ladder in §3.6.

---

## 1. The problem with the problem

ADR 0013 asked whether tool dedication makes a per-family assignment worth
solving. The 30-day screen said no
([`bench/results/dedication/README.md`](../../bench/results/dedication/README.md)),
and the first reading of that — "the matrix is too mild" — was wrong. The
generator will not write a harder balanced matrix on SMT2020 anyway (0.60 is
refused; 0.65 is the frontier), so "tighten it" was never available.

The real objection is the **shape** of the constraint, not its strength.

Qualification is **unary**: "lot L may run on tool set M(L)" filters each
(lot, tool) pair independently. A greedy sort handles that with no cleverness
at all — walk the queue in priority order, skip the lots this tool is not
qualified for, take the first that fits. The decision for tool 1 does not
depend on the decision for tool 2, so there is nothing to *assign*; there is
only a filtered sort. That is why 0.80 and 0.65 measured the same.

Two rows do separate, and they say the same thing from the other side. On
`dedication-skew-70` — the deliberately asymmetric variant, where some tools
are workhorses and others specialists — `fifo` collapses to 62.5% on-time
with 885 lot-days of tardiness while `cr` holds 85.2%. A 23-point spread,
against 2.4 points on the balanced matrix at the same fraction. So it is not
that SMT2020 cannot be made to care about dispatching. It is that
**balanced** dedication, which ADR 0013 §3.2 chose deliberately to avoid
starving a part by accident, gives every part an equal share of every family
and so removes the heterogeneity that makes any of this matter.

Even there, the sort key wins: `cr` 85.2% against the slate's 80.8%. A hard
*unary* problem is still a sort problem.

## 2. What a reticle is, and why it is a different class

One physical mask cannot be mounted on two scanners at once. So two lots
needing the same reticle cannot run concurrently **even when both scanners
are idle and both lots are fully qualified**. The value of assigning lot A to
scanner 1 now depends on what is assigned to scanner 2.

That is a coupling constraint, and a sort key cannot represent it: it scores
lots independently and commits per tool as tools free. It is also exactly
what `dispatch/include/fab/solver.hpp` has encoded since ADR 0009 —
`AddAtMostOne` over the scanner assignments grouped by reticle in the CP-SAT
path, and a `reticle_on` map in the greedy one. **Both paths have carried the
constraint from the beginning and nothing has ever set a reticle id.** This
ADR is the data half of a feature that was already built.

Moving a mask between scanners costs transport, so there is a second
decision: keeping a mask mounted for consecutive lots of the same layer saves
the move. That is a run-length tradeoff of the same kind as a setup, and a
myopic rule cannot see it either.

**Identity.** A reticle is per (part, photo layer), and the layer is the
step's `DESC` rather than its order, so a lot sent back by rework returns to
the same physical mask.

**Scope.** Station group `Litho` minus the `LithoTrack_*` coat/develop
families: 7 families, 82 scanners. `Litho_REG_*` is registration metrology in
group `Litho_Met` and holds no mask — a name-prefix rule would wrongly
include it, so the group is always consulted.

## 3. The decision

### 3.1 Mechanism

`reticles.tsv` inside `data/smt2020/overlays/<name>/`, beside the optional
`qualification.tsv`. An overlay may carry either, or both; only the empty
directory is an error. The two constraint classes are **independently
switchable on purpose** — 0013's screen turned on one thing and could not say
what it had changed, and the same mistake is available here.

The library folds into the overlay hash **only when present**, so every ADR
0013 checkpoint filename and row stamp is byte-identical and the pristine
fingerprints are untouched. A fab warmed without the masks has the wrong WIP,
for the reason 0013 §3.5 gives about the matrix.

### 3.2 Generator

`bench/tools/gen_reticles.py`. One mask per (part, layer) at a scanner step;
`--copies` sets the baseline, `--autosize --max-util U` buys a second mask
for any layer above `U` of a mask-day.

**Copies are the knob, and it is the only one that works here.** SMT2020 LVHM
has no volume mix to exploit — all ten parts release at exactly 5.70 lots/day
— so "extra masks for high-volume parts" cannot differentiate anything. Layer
demand does vary, though narrowly (15.7%–34.8% of a mask-day, median 27.0%),
and `reticles-mix` gives the busiest third a second mask: 85 layers with two,
166 with one, 336 masks over 251 layers.

The capacity check refuses at 0.95 rather than 0.90: high mask contention is
the regime being bought, and only infeasibility — a mask that cannot meet its
own demand at any schedule — is a capacity result wearing a scheduling
result's clothes.

### 3.3 Simulator

`Instance.eligible()` gains `mask_free()`, the **first narrowing that is not
static**: qualification and dedication are properties of the (lot, tool)
pair, while a mask's availability depends on what every other scanner is
doing at this instant. It is therefore asked at the decision point rather
than precomputed, which is the whole reason it is a harder problem.

`Instance.dispatch()` claims a copy, charges `transport_s` when the mask has
to move, and releases it at **lot** done rather than machine done: the mask
is in the scanner while the lot exposes and can be pulled as soon as the lot
leaves, whereas `machine_done` carries preventive maintenance and any folded
breakdown, and holding a mask through a tool's PM would block every other
scanner for a reason that has nothing to do with the mask.

### 3.4 Solver

`CTool.is_scanner` is master data, read at `set_tools` only, like
`qualified_parts`. `CLot.reticle` is lot state and rides every plan call,
because the mask a lot needs changes as it walks its route. `holds_reticle()`
is asked instead of comparing `kind()`, so a `FamilyTool` standing in for a
scanner can answer yes without impersonating `LITHO_SCANNER` everywhere else
— `kind()` also drives batch handling and the model's `tool_kinds`.

Both sides read the **same** library object the simulator enforces, which is
0013 §2's rule: if the solver and the simulator could disagree about which
lot needs which mask, a row labelled `slate` would be measuring its fallback.

### 3.5 Harness

**Scanner-scoped utilisation on every row.** Fab-wide utilisation averages a
litho constraint over 1,313 tools and hides half of it. Pristine scanners run
at **93%** — they are the bottleneck — and under `reticles-mix` the choice
between `fifo` and `cr` is worth **4.8 points of scanner utilisation against
2.3 fab-wide**. Reported for the pristine fab too, since the number only
means anything against the same number without masks.

The gates are unchanged and both held after this work: pristine `slate-cr`
still reproduces `cr` over 47,149 decisions at fp `8d77d45c4c2654a3`, every
0013 overlay hashes as before, and 97/97 C++ tests pass.

### 3.6 Runs

The ADR 0013 screen ran at 1.00× and 1.03×, below the knee, where total
tardiness over 30 days is a few lot-days across the whole fab and no
dispatcher has anything to optimise. That will not be repeated. The ladder
is:

1. **Stability first.** Warm each library 90 days under `fifo` at 1.00×. A
   library whose WIP climbs through its own warm-up cannot carry a ladder —
   it is a capacity experiment. Pristine scanners at 93% against ~78% under
   masks makes this a real risk, and it is checked before anything else.
2. **Cheap rules across a start ladder**, 90-day window, reading WIP slope in
   lots/day over the final third rather than endpoint deltas. `fifo` and `cr`
   cost ~1/10th of `slate`, so they locate the divergence point S\*.
3. **The solver only near S\***, plus one canary early enough to catch the
   `idleQ` warning below.

The claim under test is the one that justifies a solver at all: **there is a
start rate the solver can sustain with WIP stationary that the sort rules
cannot.** Throughput is not the metric — over any window short of WIP
settling it is a drain artifact, which is the trap 0013's screen fell into
(`Δlots ≡ ΔWIP`, residual zero in six of eight cells).

## 4. What this assumes

- **A mask is held for the exposure and is otherwise free to move.** No
  stocker capacity, no inspection or qualification cycle between mounts, no
  pellicle life. A real fab has all three.
- **Transport is a constant** (`--transport-s`, default 900 s) rather than a
  function of distance or AMHS contention. ADR 0008 already flattens fab
  transport to one number; this is consistent with that, not worse.
- **The library is invented.** SMT2020 ships no reticle data, so nothing here
  is validated against a real fab's mask set. A result is a real property of
  an invented library, and must be written that way.
- **Rework does not consume extra mask time** beyond returning to the same
  layer, which is right in kind and optimistic in degree.

## 5. If it turns out to be wrong

If masks do not separate the rules either, the remaining candidate with the
solver half already built is **queue-time enforcement**: SMT2020 parses CQT
columns, PySCFabSim ignores them (ADR 0008), `Instance.dispatch()` has the
block commented out, and `slate_rule.py` pins `QTIME_INERT = 1e9` precisely
because the environment never punishes a violation — while the C++ side
consumes `qtime_slack_s` all the way into `BatchTool::should_fire`.

It is the only candidate that gives the fab a way to **lose work**. Today the
worst outcome of a bad decision is a late lot, and total tardiness below the
knee is a few lot-days across 1,300 tools; with CQT enforced, a blown queue
time is scrap or rework, which is capacity destroyed. A dispatcher that
cannot lose anything is hard to beat and hard to justify.

And if neither separates them, the honest conclusion is the one ADR 0013 §7
already drafted: the real-time layer is a sort key with the downstream and
batch terms, and the optimisation effort belongs in the segment scheduler.
The mechanisms stay either way — a realistic fab needs them regardless of
which dispatcher wins.

**One warning already on the record.** In 0013's screen the slate had higher
`idleQ` than `cr` in all eight cells: it left *more* tools idle while work
they could have run waited. Below the knee that is harmless. Under load it is
the mechanism by which a rule loses capacity, and if it persists the slate
will diverge *earlier* than `cr`, not later. On `dedication-skew-70`, the one
configuration so far where dispatching visibly matters, `cr` already beats
the slate 85.2% to 80.8% on-time. The ladder should be read with that in mind
rather than against it.
