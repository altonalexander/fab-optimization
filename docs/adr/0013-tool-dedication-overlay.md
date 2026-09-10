# 0013 — Tool dedication overlay: make the fab worth assigning, then measure

**Status:** Proposed, 2026-09-10. Plan only; no code, no numbers yet. This is
the first item of `docs/NEXT.md` §2 (data overlay) and the experiment ADR
0012 deferred to: its "what would overturn this" names *an overlay fab where
the assignment solver still adds nothing over a sort*. This ADR is how that
condition gets tested.

Written for hand-off: a different agent, on a machine with enough cores to
run the rows continuously, should be able to pick this up from this page,
`docs/NEXT.md` §2, and the files named in §6 without the conversation that
produced it.

---

## 1. The problem

ADR 0012 §3 established why the slate cannot beat a sort key on the pristine
testbed: tools within a SMT2020 family are identical. Same speed, no
dedication, no reticles, one setup time per layer, q-times parsed but not
enforced (ADR 0008). "Which lot to which tool" collapses to "which lot next",
and a per-family assignment has nothing to assign. The solver's value over
`cr` was near zero by construction, and further dispatcher tuning was
stopped until the fab is worth scheduling.

Real fabs are not like that. A recipe is qualified on a subset of a family's
tools — scanners and implanters first, but etch chambers and diffusion
furnaces too — and a myopic rule pays for it: a machine-centric sort spends
the one tool a lot could run on serving a lot that could have run anywhere,
and the inflexible lot waits. That is a matching problem. It is the case an
assignment solver exists for and a sort key cannot see.

So the question is not "is slate better" but "does dedication turn the
per-family problem into one where assignment matters, and by how much." The
answer decides whether CP-SAT stays in the real-time layer (ADR 0012) and
whether the segment scheduler (NEXT §3) is worth building.

## 2. Options

**Edit the testbed.** Add a qualification column to `tool.txt` or the routes.
Rejected: SMT2020 has no such column, the pristine LVHM must stay the
baseline row, and `data/smt2020/` is symlinked into the simulator precisely so
there is one load (top-level README: if that symlink is broken, stop).

**Per-lot allowed-tools on the wire.** The twin computes which tools a lot may
use and forwards the list with each lot. Rejected. Qualification is master
data, not state: the production design already loads a tool master once
through `ToolFactory` (`dispatch/include/fab/tool_factory.hpp`) and every
tool class in `machine_config.hpp` carries its own qualified-recipe list and
answers `RecipeNotQualified` itself. The stream carries state only — tool
up/down and setup on the compacted `fab.tool.state` topic, lot readiness on
`LOT_READY`, the WIP snapshot at cold start (ADR 0003, which also rejected
having the dispatcher ask the simulator for anything it can know on its
own). Forwarding a derived view per lot would also re-send the same matrix
thousands of times per planning cycle across the ctypes boundary, which is
the marshalling cost ADR 0009 measured as the run's dominant cost.

**Overlay directory, applied by flag, read by both sides.** Chosen. A
qualification table beside the testbed, never inside it; the simulator's
eligibility predicate and the solver's tool structs are both populated from
the same overlay object. Same-data guarantee by construction, same principle
as the dataset symlink.

## 3. The decision

### 3.1 Overlay mechanism

`data/smt2020/overlays/<name>/` holding:

- `qualification.tsv` — `STNFAM`, `PART`, `STNS` (qualified machine names in
  that family, `;`-separated). A family/part pair absent from the table is
  fully qualified, so an empty table is the pristine fab.
- `provenance.json` — generator parameters (scope, fraction, floors, seed),
  generator version, the pristine dataset hash, and a content hash of the
  table. The hash keys the warm-up checkpoint (§3.5) and is stamped on every
  result row.

`--overlay <name>` on `bench/tools/compare.py` and `bench/tools/sim_feed.py`.
No overlay means today's behaviour, byte-identical.

### 3.2 Generator

`bench/tools/gen_overlay.py`: builds `qualification.tsv` from
`tool.txt.1l` and `part.txt`.

- **Balanced, not iid.** For each family in scope, shuffle its machines with
  the overlay seed and deal parts round-robin, so each part keeps a uniform
  share of the family's capacity. Independent coin flips randomly starve a
  part at a bottleneck family and turn a dispatching experiment into a
  capacity one. A `--skew` mode exists for a deliberately hard variant, off
  by default.
- **Fraction** of a family's machines qualified per part; sweep axis.
- **Floors.** At least two qualified machines per (family, part) where the
  family has two; every machine qualified for at least one part. Without
  these, capacity is silently deleted.
- **Scope.** `--families litho,implant` (station groups `Litho`, `Implant`)
  or `all`.
- **Capacity check, printed and enforced.** Per family and part: effective
  machine share against the pristine utilization at the operating point
  (1.03× starts, ADR 0012). Refuse to write an overlay that pushes any
  family past 90%. The generator's own seed is separate from the simulation
  seed so the matrix is a property of the dataset, identical for every rule.

First overlays:

| name | scope | fraction |
|---|---|---:|
| `dedication-litho-50` | litho + implant | 0.50 |
| `dedication-all-50` | all families | 0.50 |
| `dedication-all-33` | all families | 0.33 |

### 3.3 Simulator: one eligibility predicate

PySCFabSim narrows a family only through the static lot-to-lens dedication
(`Lot.dedications`, set from `SVESTN`/`FORSTEP`), checked in exactly one
form in `dm_lot_for_machine.py:13`, `dm_machine_for_lot.py:23`,
`greedy.py:116-127` and `greedy.find_alternative_machine`. Replace those with
one `instance.eligible(lot, machine)` whose default is the existing
dedication check; the overlay loader adds the qualification lookup
`(machine.family, lot.part_name) -> set(machine.idx)`. Both dispatch
managers, the alternative-machine search and the greedy dedication branch
call the predicate. This is the only change to the vendored simulator;
record it in `baselines/pyscfabsim/UPSTREAM.md`.

Batches are keyed on `step_name + part_name` (ADR 0009), so every member of
a batch shares qualification and batch formation needs no change.

### 3.4 Solver: the tool knows what it can run

Add `qualified_parts` to the tool struct in the C ABI
(`dispatch/src/slate_capi.cpp`, `set_tools`), filled by
`slate_rule._tool_dict` from the same overlay object the instance loaded.
Tools are sent once at `set_tools` and re-synced only on state change, so
the matrix crosses the boundary once. `FamilyTool::evaluate`
(`dispatch/include/fab/family_tool.hpp`) today checks family and setup only;
it gains the qualified-recipe rejection the other tool classes already have.
Nothing is added to the lot payload — the lot carries `part` already. A
`test_main.cpp` case: an unqualified part is rejected with
`RecipeNotQualified`; an empty list means fully qualified.

For the production path (`sim_feed.py` over ZeroMQ/Kafka), the overlay
produces the tool-master JSON `ToolFactory` loads. The stream is untouched.

Per-lot dedication stays where it is: it is decided at dispatch time for one
lot, so it is lot state and rides `LOT_READY` (the C++ `Lot` already has the
analogous `reticle` field). Qualification on the tool, dedication on the lot.

### 3.5 Harness

- **Checkpoint keyed by overlay hash.** A fab warmed 90 days without the
  matrix has the wrong WIP (ADR 0012 §4.5 reasoning). `sim_feed.py`'s
  checkpoint name gains the overlay hash; `compare.py --warmup-days 90
  --overlay X` builds it under `fifo` if missing, as today.
- **Gate under the overlay.** `slate-cr` must reproduce `cr`'s decisions from
  the overlay checkpoint exactly, or `compare.py` prints no table.
- **Pristine unchanged.** With no overlay, `fifo` and `cr` from the existing
  checkpoint reproduce today's fingerprints exactly. This is the check that
  the predicate refactor changed nothing.
- **One new KPI on every row:** family hours idle while qualified WIP for it
  waited elsewhere in the family. This is the quantity a matching solver and
  a sort key should separate on; throughput and cycle time follow from it.
- **Row provenance.** Overlay name and hash, and starts scale, on every
  result row and on the Results tab. An overlay row is never laid over a
  pristine one unlabelled.
- Coverage stays printed. Expect it to fall: more tokens become unusable when
  tools free in an unplanned order.

### 3.6 Runs

From each overlay's day-90 checkpoint, LVHM seed 0, 30 days, headless, one
process per row:

| overlay | starts | rules |
|---|---:|---|
| pristine (existing rows) | 1.00×, 1.03× | fifo, cr, slate |
| `dedication-litho-50` | 1.00×, 1.03× | fifo, cr, slate |
| `dedication-all-50` | 1.00×, 1.03× | fifo, cr, slate |
| `dedication-all-33` | 1.03× | fifo, cr, slate |

Then 120 days at 1.03× for the overlay that separates the rules most, seeds
0 and 1, and the pressure ablation `slate:none,slate:due,slate:full` on it
(ADR 0012 left it unrun; on a dedicated fab it finally has something to
separate). Each `slate` row is ~40 min wall clock per 30 days on the
development box; the schedule above is a few hours on one machine and
embarrassingly parallel across rows, which is why this is written for a
box with more cores.

## 4. What this assumes

- A balanced 50% matrix leaves every family below 90% effective utilization
  at 1.03× starts. If the generator's capacity check refuses, drop the
  fraction rather than the floors.
- Qualification per (family, part) is the right granularity to start. Per
  (family, part, step) is closer to reticle dedication and ~4,000 pairs;
  reticles are their own NEXT §2 item and should not be smuggled in here.
- Static qualification is enough to answer the ADR 0012 question. Time-varying
  qualification (a tool losing a recipe after PM until re-qualified) is state,
  would ride the compacted tool-state topic the way online/offline does, and
  is a v2 the numbers here decide.
- The absolute numbers get worse for every rule (fewer eligible tools, more
  setups, more idle-with-WIP time). The gap is the result, not the level.

## 5. How to know whether it is right

The experiment is a success if either outcome is clean:

- **Assignment matters.** On `dedication-all-50` at 1.03×, `slate` holds or
  widens its completions and cycle-time margin over `cr` (ADR 0012: +1.0%,
  −1.6 d on the pristine fab at 120 days) and the idle-with-qualified-WIP
  KPI is materially lower for `slate` than for `cr` and `fifo`. `cr`'s
  tardiness rises more than `slate`'s, because its endangered lot is the one
  stuck behind a bad machine choice.
- **It does not.** `slate` within noise of `cr` on every overlay row. Then
  ADR 0012's overturn condition is met: CP-SAT leaves the real-time layer and
  is kept for the segment schedule only.

Failure modes that would make the number meaningless, and their checks:

- The solver did not see the matrix and the row measured the fallback again
  (the ADR 0012 / summary §4.4 failure). Check: coverage on the row, and the
  `test_main.cpp` rejection case.
- Capacity, not dispatching, dominated. Check: the generator's utilization
  print; every rule collapsing together on a row is this.
- Different fabs at day 90. Check: checkpoint keyed by overlay hash; gate
  re-run from it.
- The predicate refactor changed the pristine answer. Check: fingerprints.

## 6. Where the work lands

| step | files | acceptance |
|---|---|---|
| 1 predicate | `baselines/pyscfabsim/simulation/instance.py`, `dispatching/dm_*.py`, `greedy.py`, `UPSTREAM.md` | pristine fingerprints unchanged |
| 2 overlay + flag | `data/smt2020/overlays/`, `bench/tools/compare.py`, `bench/tools/sim_feed.py` | `--overlay` loads, checkpoint keyed by hash |
| 3 generator | `bench/tools/gen_overlay.py` | three overlays written with provenance; capacity check printed |
| 4 solver | `dispatch/src/slate_capi.cpp`, `dispatch/include/fab/family_tool.hpp`, `dispatch/src/test_main.cpp`, `bench/tools/slate_rule.py`, `scripts/build-slate.sh` | rejection test passes; `slate-cr` gate passes under overlay |
| 5 KPI + provenance | `bench/tools/sim_feed.py` (`FeedPlugin._kpi_sample`), `bench/tools/publish_runs.py`, Results tab | idle-with-qualified-WIP on every row; overlay badge on the dashboard |
| 6 runs | `bench/results/dedication/` with its own README, like `starts/` | table in §3.6 filled |
| 7 write-up | this ADR's status; `summary.md` §7.4; NEXT §2 boxes | outcome against §5 stated in one sentence at the top |

Order: 1, then 2 and 3 together, 4, 5, then 6 and 7. Steps 1–5 are a day or
two of code. The KPI definition lives once, in `FeedPlugin._kpi_sample`
(summary §4.6); do not add a second sampler.

Deferred on purpose: reticles, q-time enforcement, sequence-dependent track
setups, time-varying qualification, and any change to the fallback, the
urgency terms or the tier ordering. Tuning the dispatcher before the fab is
worth scheduling is the mistake ADR 0012 named.

## 7. If it turns out to be wrong

If dedication alone does not separate the rules, the next overlay is
reticles (exclusive across scanners, a transport delay between them), which
adds a shared resource the sort key cannot reason about at all. If that also
does not separate them, the real-time layer is a sort key with the
downstream and batch terms, and the optimisation effort moves entirely to
the segment scheduler. Either way the overlay mechanism, the predicate and
the tool-master field are kept; they are what a realistic fab needs
regardless of which dispatcher wins.
