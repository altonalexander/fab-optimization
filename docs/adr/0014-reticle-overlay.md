# 0014 — Reticles: the coupling constraint, and the loss function the fab lacks

**Status:** Implemented 2026-09-11. Successor to ADR 0013, whose
qualification matrix did not separate the rules. Written for hand-off: the
mechanism, the generator and both solver paths are in place; what remains is
the ladder in §3.6.

**Read §6 before §2.** §2's argument — that a reticle is a *coupling*
constraint and therefore a different class of problem from 0013's matrix — is
sound but incomplete, and the first rows showed why. A coupling constraint
that never blocks anything costs nothing: on LVHM at 1.00×, with transport
zeroed, the mask library is indistinguishable from the pristine fab. The
missing condition is that the resource be scarce enough that blocking *idles
a tool*, which on a high-mix fab requires volume the default scenario does
not have. §3.6 is written around producing that condition rather than
assuming it.

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

A mask is the **first narrowing that is not static**: qualification and
dedication are properties of the (lot, tool) pair, while a mask's
availability depends on what every other scanner is doing at this instant.

**It therefore must NOT go in `Instance.eligible()`,** which is where it was
put first and which cost a day. ADR 0013 calls `eligible()` "the single place
the question is asked", and that is true of the *static* narrowings. But
`eligible()` is not only consulted at the moment of dispatch: the dispatch
manager calls it once per lot, when the lot becomes available, to decide
which machines the lot queues on (`dm_lot_for_machine.free_up_lots`). Put a
time-varying test there and a lot that happens to arrive while its mask is
busy is registered against **no machine at all** — permanently, because
`free_up_lots` runs once per lot per step and nothing re-offers it.

That does not present as a crash. It presents as a fab quietly dying:
utilisation decaying 80% → 47% over four days, scanner utilisation 96% → 46%,
WIP climbing, and — the tell — masks held *falling*, because fewer and fewer
lots were running. It was diagnosed as a capacity cliff twice before the
shape of the decay gave it away. A leak would have shown masks accumulating;
these were draining.

So the predicate lives in three places instead:

- `mask_free()` is asked where the choice is actually made:
  `get_lots_to_dispatch_by_machine` filters this instant's candidates, and
  `find_alternative_machine` checks the mask is free on the tool it wants to
  move a batch to.
- `wake_mask_waiters()` re-offers idle scanners when a mask comes back. A
  machine that found nothing runnable is dropped from `usable_machines`, and
  only finishing a job puts it back — which never happens to a tool that is
  already idle. A mask released elsewhere in the fab is exactly the kind of
  state change no event of the tool's own will announce.
- `eligible()` stays purely static, so the pristine path is untouched and the
  ADR 0013 fingerprints still hold.

**The general lesson, worth more than the bug:** a *resource* cannot be
modelled as an eligibility filter in this simulator. Eligibility is cached
into queue membership; resources need an explicit release-and-wake.

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

**Per-part KPIs on every row.** On a high-mix fab a fab-wide average is
*structurally incapable* of showing a saturated mask. When one part's mask
blocks a lot, the scanner takes one of the ~250 other layers and stays busy;
the fab's throughput and utilisation do not move. The whole cost lands on the
blocked part's own cycle time and on-time. The first mix-shift run was read
as a no-op on exactly this mistake, so `kpis()` now returns `by_part`
alongside the fab-wide figures and the ramped parts are what gets read.

**The start mix is part of the warm-up, and keys the checkpoint.** A ramp
re-times *future* releases only. Cycle time is ~37 days, so a window opened
on a fab warmed at the uniform mix completes nothing but pre-ramp lots and
the experiment cannot see its own effect — an 8-day window measured lots
released 37 days before the ramp existed. `--starts-part` is therefore
applied *through* the warm-up and `mix_key()` puts a hash of it in the
checkpoint name, for the same reason the overlay hash is there (0013 §3.5):
a fab warmed under different conditions has the wrong WIP. An empty mix
contributes nothing, so every existing checkpoint filename is unchanged.

The gates are unchanged and both held after this work: pristine `slate-cr`
still reproduces `cr` over 47,149 decisions at fp `8d77d45c4c2654a3`, every
0013 overlay hashes as before, and 97/97 C++ tests pass.

### 3.6 Runs

The ADR 0013 screen ran at 1.00× and 1.03×, below the knee, where total
tardiness over 30 days is a few lot-days across the whole fab and no
dispatcher has anything to optimise. That will not be repeated. The ladder
is:

1. **Put the masks into contention first, or nothing else is worth running**
   (§6). A per-part ramp, not a uniform one: `part_1` and `part_2` at 3.3×
   takes their masks from ~27% to ~89% of a mask-day, and the other eight
   parts scale to 0.425 so total starts stay at 57 lots/day. Capacity is
   held constant and only the concentration of demand changes, which keeps
   this a scheduling experiment. The pristine control runs the identical
   mix; transport is zero, so what is measured is exclusivity alone.
2. **Warm 90 days under that mix and measure days 90–180** (§3.5). A window
   opened on a uniformly-warmed fab completes only pre-ramp lots.
3. **Cheap rules first.** `fifo` and `cr` cost ~1/10th of `slate` and settle
   the prior question — does the constraint bind at all once its masks are
   saturated? `slate` runs only if the answer is yes.
4. **Read `by_part` for the ramped parts**, not the fab-wide row, which
   cannot move (§3.5).

The claim under test is the one that justifies a solver at all: **with a
scarce shared resource, the solver protects the ramped parts' flow better
than a sort key can.** Throughput is not the metric — over any window short
of WIP settling it is a drain artifact, which is the trap 0013's screen fell
into (`Δlots ≡ ΔWIP`, residual zero in six of eight cells) — and neither is
fab-wide utilisation, which on a high-mix fab cannot register a mask at all.

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

## 6. What the first rows said, and the condition §2 left out

§2 argues a reticle is a coupling constraint and therefore a different class
of problem from ADR 0013's matrix. That argument is sound and **it is not
sufficient**, which the first rows established before any ladder was run.

**On LVHM at 1.00×, the coupling costs nothing.** With transport set to zero
— isolating exclusivity from mask movement — `reticles-1x` is
indistinguishable from the pristine fab: 493 lots against 495, 84.2% on-time
against 84.7%. Copies make no difference either (251 / 336 / 502 masks give
77.9 / 77.8 / 78.0 fab-wide utilisation). The entire measured cost of the
library was transport, a setup-like penalty that `cr` absorbs completely
(92.6% against 92.7%).

**The condition §2 left out is that the constraint must be able to idle a
tool.** A mask that blocks a lot has cost the fab nothing if the scanner
simply runs a different one. On LVHM there are 251 (part, layer) masks across
82 scanners and each mask sits at ~27% of a mask-day, so a blocked lot is
always replaceable and the exclusivity never bites. Being a coupling
constraint is necessary for the solver's case and not enough; the resource
also has to be scarce enough that blocking propagates.

That is a property of **mix and volume together**, and the two SMT2020
scenarios bracket it:

| | parts | masks | load per mask at 1.0× |
|---|---:|---:|---|
| LVHM (high mix, low volume) | 10 | 251 | ~27% — never contends |
| HVLM (low mix, high volume) | 2 | 60 | **169%** — infeasible at one copy |

HVLM needs a second copy per layer before it is even feasible, and then runs
at 84–93%. The generator refusing one-mask-per-layer on HVLM is it
independently rediscovering why fabs buy duplicate masks for high-runner
layers.

**For a high-mix fab the lever is volume, not mix.** Switching to HVLM would
answer a question about a different fab. The configuration that puts a
high-mix fab into the contended regime is a per-part ramp: `--starts-part`
raises one or two parts until *their* masks saturate (mask load is linear in
that part's rate, so ~3.3× takes 27% to ~89%), while the remaining parts
scale down to hold total starts at 57 lots/day — so capacity is unchanged and
what moves is the concentration of demand. That is the only configuration in
which the §2 argument can be tested on the fab being modelled, and it is what
§3.6's ladder now runs on.

If the constraint does not bind even there, the coupling argument is
exhausted on this testbed and §5 applies.
