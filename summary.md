# Measuring a dispatching rule against a fab

Every time a machine in a wafer fab frees up, something chooses which waiting
lot goes next. That choice is the **dispatching rule**. On the SMT2020 LVHM
fab modelled here it is made about **16 million times** in a two-year run.

Claiming one rule beats another is easy. Measuring it is not, and almost every
way of trying is wrong in a way that does not announce itself. This is an
account of building a harness that measures it honestly — including the four
times the harness was quietly measuring the wrong thing, and how each was
caught.

---

## 1. The fab, in numbers

Everything below is LVHM, the standard scenario ([`adr/0001`](docs/adr/0001-lvhm-default-scenario.md)),
read from `data/smt2020/SMT2020_LVHM`:

| | |
|---|---|
| machines | **1,313** |
| station families | **106** (≈12 machines each) |
| steps per route | **~400**, across 10 routes |
| lots in initial WIP | **2,154** |
| lots completed in 730 days | **~39,600** |
| **dispatch decisions in 730 days** | **~16,000,000** |

A lot makes hundreds of passes through the same few hundred machines,
revisiting the same toolsets at different stages. The queue you join depends on
every decision made before it. Machines break down, need preventive
maintenance, require setup changes between recipes, and some process wafers in
batches that must be filled before they fire.

That re-entrancy is why a fab cannot be scheduled once and then followed, and
why the interesting question is not "what is the optimal schedule" but "what
rule, applied 16 million times under disturbance, ends up with the best fab."

---

## 2. What a fair comparison requires

Two rules are comparable only if **everything else is identical**: same demand,
same breakdowns, same machine set, same routes, same horizon, same definition
of every KPI. In a real $10B fab this is impossible — you get one fab and one
history. In a simulator it is possible, and it is the entire reason the
simulator is here.

The repository enforces the first half structurally. `data/smt2020/` is a
single directory symlinked into the vendored simulator, so the dispatcher and
the simulator cannot read different loads. As the top-level README puts it: if
that symlink is ever broken, stop.

The second half — same horizon, same KPI definitions — is what a harness has
to enforce, and it is where the failure modes live.

### The failure mode this project already had

Before this work, `dispatch/` and `baselines/pyscfabsim/` were two programs
that shared only a dataset:

```
  PySCFabSim                                 fabdisp
  ─────────────────────────────              ───────────────────────
  discrete-event, 730 sim-days               10s planning cycle
  routes, setups, batching, PM,              single-period assignment
  rework, due dates, breakdowns              (no time index)
        │ rule = sort key                          │ synthetic load
        │ (fifo | cr | lifo | PPO)                 │ ~96 lots
        ▼                                          ▼
  cycle time, throughput,                    assignment objective,
  tardiness, on-time %, util                 lots assigned, ns latency
```

Different generators (1,625 vs 859 feasible pairs at ~200 lots), different
horizons, different KPIs. A headline "+34.4%" measured 21–30% across the two
harnesses depending on which pair you compared. The number was not wrong so
much as **meaningless** — there was no single question it answered.

The fix ([`adr/0002`](docs/adr/0002-dispatcher-inside-pyscfabsim.md)) is
structural, not statistical: put the dispatcher *inside* the simulator, so
there is one generator, one horizon, one KPI set, and the only thing that
differs between rows is the dispatching decision.

---

## 3. The harness

```
bench/tools/compare.py  --days 30 --rules fifo,cr,slate
        │
        │  one PySCFabSim instance per rule, identical dataset/seed/horizon
        ▼
  ┌──────────────────────────────────────────────────────────────┐
  │  greedy.py:71                                                │
  │     lot.ptuple = ptuple_fcn(lot, time, machine, setups)      │
  │     wl = sorted(machine.waiting_lots, key=lambda k: k.ptuple)│
  │                        │                                     │
  │        ┌───────────────┴──────────────┐                      │
  │        ▼                              ▼                      │
  │  fifo / cr / lifo              slate_rule.SlateRule          │
  │  (upstream sort keys)                 │                      │
  └───────────────────────────────────────┼──────────────────────┘
                                          │ once per planning cycle
                                          ▼
  ┌──────────────────────────────────────────────────────────────┐
  │  libfabslate.so  (C ABI, ctypes)                             │
  │     Planner::plan_by_family  →  CP-SAT per station family    │
  │     Slate  ──  lot → {primary, alternate, rank}              │
  └──────────────────────────────────────────────────────────────┘
```

The rules under test:

| rule | what it is |
|---|---|
| `fifo` | oldest waiting lot first |
| `cr` | critical ratio — slack ÷ remaining work; the most endangered lot first |
| `lifo`, `random` | upstream, for reference |
| `slate` | CP-SAT assignment, rebuilt every N simulated seconds |
| `slate:none/due/full` | the same, with each tier of information ablated |
| `slate-cr` | **the validation gate** — see §5 |

---

## 4. Four ways the measurement was silently wrong

This is the part worth reading. Each of these produced *plausible numbers*
while measuring something other than what the label claimed.

### 4.1 The tuple shape is load-bearing

The integration point looks like a clean seam: supply a `ptuple_fcn`, get
sorted. It is not. `greedy.py:83-89` reaches **into** the returned tuple to
form batches — it reads `ptuple[0]` (the minimum-run gate) and splices
`ptuple[2:]` (the priority rule) into its batch ordering.

So a rule returning a differently-shaped tuple silently changes **batch
formation**, and the comparison is then measuring two changes at once while
reporting one. `slate_rule` copies slots 0–2 verbatim from the upstream rules
and contributes only at slot 3+.

### 4.2 KPIs are a vacuous equality check on short horizons

The first validation run compared `cr` against a passthrough rule over 2
simulated days and reported a clean pass. It was worthless: with ~400 steps per
route, **no lot finishes in 2 days**, so every rule scores throughput 0, cycle
time 0, on-time 0. A completely broken harness would have "validated".

The gate now fingerprints the **entire dispatch sequence** — a rolling hash of
`(time, machine, dispatched)` at every decision point. It diverges on the first
differing choice, whether or not anything has finished.

### 4.3 Coverage measured the wrong denominator

The first coverage metric asked: *of the lots the rule was consulted about, how
many had a slate token?* It read 3%, which looked catastrophic.

It was the wrong question. A machine's `waiting_lots` can hold hundreds of
lots, while the slate — which assigns at most one lot per free tool — holds a
token for one of them. That metric is bounded by queue depth by construction
and says nothing about who decided.

The right question is *at a decision point for this machine, did the slate have
a pick?* — the share of decisions the optimizer actually made.

### 4.4 Planning only for free tools measured the fallback

With coverage now measured correctly, it read **6.3%**. The cause: the slate
was built only over `instance.usable_machines`, the handful of machines
awaiting a decision at that instant, so it held a few tokens per cycle and
~94% of decisions fell through to the fallback score.

A run labelled `slate` was, in truth, mostly measuring its fallback.

The fix is also what the production dispatcher does: plan across the whole fab,
then serve lookups as tools free. A token for a busy machine costs one variable
and is never consulted; a machine that frees between rebuilds now finds a pick
waiting. **Coverage 6.3% → 56.6%.**

> This is why coverage is printed on every row. A rule that cannot answer for
> most decisions is not the rule you think you measured, and nothing else in
> the row means what it says.

---

## 5. The validation gate

Before trusting any number from a new rule, prove the *harness* does not change
the answer.

`slate-cr` routes through the entire slate call path — same signature, same
tuple shape, same rebuild hook, same fallback plumbing — but returns CR's
ordering. It must reproduce `cr` exactly.

```
VALIDATION PASS: slate-cr reproduced cr's 47,149 dispatch decisions exactly
                 (fp 8d77d45c4c2654a3). The harness does not change the answer.
```

If this fails, no other row means anything, and `compare.py` says so in those
words rather than printing a table.

---

## 6. Where the line between Python and C++ falls

Recorded in full in [`adr/0009`](docs/adr/0009-slate-rule-hybrid-split.md).
Three measurements decided it.

**The rebuild is the hot path, not the lookup.** At N=60 simulated seconds a
730-day run is ~1.05M planning cycles. The 16M decision points are dict lookups
against a snapshot and never cross a language boundary at all. This inverts
ADR-0002's stated reason for preferring pybind11 over IPC.

**Per-family decomposition is a precondition, not an optimization.** A lot at
step *s* is eligible only for machines in `step.family`, so the assignment
matrix is block-diagonal and splitting it is **exact**, not heuristic. Whole
fab: ~2,500 lots × 1,313 machines, where CP-SAT sits on its time limit
returning a best incumbent. Per family: ~25 lots × ~12 machines.

**The models did not line up, and that was the real cost.** `fabdisp::Lot`
could not express what SMT2020 requires:

| gap | before | reality |
|---|---|---|
| setup | `(recipe == current) ? 0 : flat` | asymmetric matrix; A→B ≠ B→A |
| minimum run length | *absent entirely* | `greedy.py:120` calls it "extrem wichtig" |
| due dates | *no field* | the benchmark is against CR, a due-date rule |
| batch key | recipe | step + part (`greedy.py:80`) |

The binding was never the expensive part. Closing these was, and it is genuine
improvement to the dispatcher independent of any benchmark.

The resulting split follows a line fabdisp had already drawn — `Lot::priority`
was documented as coming *"from the tactical urgency vector"*, i.e. computed
upstream:

```
Python                                   C++
────────────────────────────             ────────────────────────────
routes, due dates, remaining             tool eligibility model
work, downstream congestion,             CP-SAT assignment, per family
batch-fill pressure
        │                                        │
        └───► priority, qtime_slack ─────────────┘
              C ABI, once per planning cycle
```

---

## 7. Results

### 7.1 Solver budget: less is more

Three simulated hours, LVHM, per-family solve budget swept:

| budget | plan time | coverage |
|---|---|---|
| 50 ms | 20.5 s | 56.2% |
| 20 ms | 12.3 s | 58.6% |
| **5 ms** | **6.3 s** | **58.8%** |

5 ms is **3.3× faster with slightly better coverage**. A per-family model is
small enough that CP-SAT closes it well inside 5 ms; the remaining budget went
on proving optimality nobody collects. The decomposition argument, confirmed by
measurement.

### 7.2 Head to head — LVHM, 30 days, seed 0

| rule | cycle time (d) | throughput | on-time % | tardiness (lot·d) | coverage | wall |
|---|---|---|---|---|---|---|
| fifo | 22.822 | 66 | 98.48 | 0.1 | – | 168 s |
| cr | 22.008 | 55 | **100.00** | **0.0** | – | 173 s |
| **slate** | **21.821** | **70** | 98.57 | 1.5 | 49.6% | 2,698 s |

**Read this as a working harness, not as a result.** The caveats are larger
than the differences:

- **30 days is the fill-up transient**, not steady state. Only 55–70 lots
  finish, out of 2,154 in initial WIP, and every one started mid-route. Cycle
  time here is dominated by initial conditions, not by dispatching.
- **One seed.** A 15-lot spread on ~60 completions is inside what a seed change
  could move.
- **Coverage 49.6%** — about half of `slate`'s decisions came from the fallback.
- `slate` is **15.6× slower in wall clock** than `cr`.

The one thing that looks like signal: **`slate` is the worst row on tardiness**
while winning throughput and cycle time. Due-date pressure *is* in the Tier-1
urgency term, so either that term is too weak against a throughput-shaped
objective, or the per-cycle assignment's blindness to sequencing is costing
exactly what ADR-0002 predicted it might:

> *the solver model has no time index and cannot sequence. Against CR over 730
> days with setup minimum-run-lengths, a per-cycle assignment blind to ordering
> may lose on cycle time while winning on per-cycle objective. That is an
> informative result, not necessarily a bug.*

The pressure ablation (`--rules slate:none,slate:due,slate:full`) separates
those two explanations. It has not been run.

---

## 8. What this does not measure

Stated so nobody has to discover it later:

- **730-day steady state.** Everything above is 30 days of transient. The
  published LVHM figures assume a warm-up reset at one year; these numbers are
  not comparable to them and `compare.py` prints that warning itself.
- **Queue-time constraints.** PySCFabSim parses CQT and does not enforce it
  ([`adr/0008`](docs/adr/0008-what-pyscfabsim-simplifies.md)), so the q-time
  term in the C++ cost function is deliberately held inert rather than
  optimising against a signal the environment never punishes.
- **Transport.** One uniform draw for the whole fab; delays are a 400-station
  pseudo-toolset.
- **The other evidence track.** HSMS/SECS-II, the four network zones and the
  ~200 ns fast path have no representation in a discrete-event sim and stay on
  their own track (`make hsms-test`, the latency histogram in `e2e_main.cpp`).
  Two tracks is correct; one harness should not be made to prove both.

---

## 9. Reproducing it

```bash
make -C dispatch slate          # builds libfabslate.so (links OR-Tools if present)
make -C dispatch test           # 88 C++ tests

VENV=baselines/pyscfabsim/.venv/bin/python3

# 1. the gate. Never skip this.
$VENV bench/tools/compare.py --days 2  --rules cr,slate-cr

# 2. the comparison
$VENV bench/tools/compare.py --days 30 --rules fifo,cr,slate

# 3. publish to the dashboard's run store
dispatch/api/.venv/bin/python3 bench/tools/publish_runs.py \
    bench/results/compare_SMT2020_LVHM_seed0_30d.json
```

Runs then appear on the dashboard's **Results** tab, and the live ready pool
can be planned through the same `libfabslate.so` from the **Slate** tab.

---

## 10. The point

A dispatching rule that wins on a benchmark you built yourself has proven
nothing. What makes a comparison worth anything is the machinery that makes it
*hard* to be accidentally right:

- one generator, one horizon, one KPI set, enforced structurally
- a validation gate that fails loudly when the harness changes the answer
- **coverage reported on every row**, so a result that mostly measured its own
  fallback cannot hide
- `solver_linked` recorded with every run, so a run that silently fell back to
  greedy is never comparable to one that did not
- caveats printed by the tool itself, not remembered by the person reading it

Four times during this work the numbers looked fine and were measuring the
wrong thing. Each was caught by an invariant, not by suspicion. That is the
argument for building the invariants first.
