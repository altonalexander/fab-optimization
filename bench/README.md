# bench — dispatcher vs. baseline

The comparison only means something if both sides read `data/smt2020/`. That is
the invariant this directory exists to protect.

## Status

**The harness exists.** `tools/compare.py` runs any set of rules — including
the CP-SAT slate — on one dataset, one seed and one horizon, and prints the
table. The design decisions behind it are in
[`docs/adr/0009`](../docs/adr/0009-slate-rule-hybrid-split.md).

```
python3 bench/tools/compare.py --days 92  --warmup-days 90 --rules cr,slate-cr  # validate first
python3 bench/tools/compare.py --days 120 --warmup-days 90 --rules fifo,cr,slate
```

It needs `dispatch/libfabslate.so`, built by `dispatch/build-slate.sh`
(or `make -C dispatch slate`), and the baseline's venv for pandas.

### The validation gate

`slate-cr` routes through the slate's call path — same signature, same tuple
shape, same rebuild hook — but returns CR's ordering. It **must** reproduce
`cr` exactly, and `compare.py` checks this by fingerprinting the whole dispatch
sequence rather than comparing KPIs (on a short horizon no lot has finished and
every rule scores 0, which would let a broken harness "validate").

Measured, 2 days from day 0, LVHM seed 0: **47,149 decisions, identical, fp
`8d77d45c4c2654a3`**; 2 days from the day-90 checkpoint: **42,139 decisions,
identical, fp `514cf477c555b63c`.** If this ever fails, nothing else in the
table means anything.

### What was still open, and what closed it

- ~~Reconcile the instance generators.~~ Moot. There is now ONE generator:
  every rule runs inside PySCFabSim, so the 1,625-vs-859 feasibility-density
  mismatch cannot arise.
- ~~Map PySCFabSim's metrics onto the dispatcher's objective.~~ Done by
  construction, not by translation. The dispatcher inherits the simulator's
  routes, horizon and KPIs; `compare.py` computes cycle time, throughput,
  on-time % and tardiness from `instance.done_lots` for every row identically.

### Still open

- **730-day runs are expensive.** Measured on LVHM: ~1,440 planning cycles per
  simulated day at N=60s, ~5 ms of CP-SAT each across the dirty families;
  30 slate days from the checkpoint is ~40 min of wall clock against ~4.5 min
  for `fifo` or `cr`. Parallelising the per-family solves is the obvious
  next lever and is not done.
- **Coverage is ~47%, not ~100%.** That is the share of decision points where
  the slate had a pick for the machine being asked; the rest fall back to a
  solver-consistent score. It is reported in every row, because a low-coverage
  row measures the fallback more than it measures the slate.
- The pressure ablation (`slate:none` / `slate:due` / `slate:full`) is wired
  but has not been run as a full ladder.
- **One seed.** Everything below is seed 0.

### The shared warm-up

Every rule starts from the **same** fab. `--warmup-days 90` resumes the
simulator from the checkpoint `sim_feed.py` wrote at day 90 under `fifo`
(`bench/snapshots/SMT2020_LVHM_seed0_fifo_Demand_day90_h180.ckpt`: event
queue, tool setups, pending breakdowns, RNG, and the feed's per-lot books) and
the rule under test takes over from there. Same WIP, same tool states, same
breakdowns still to come; the only difference between rows is the dispatching
decision from day 90 on. It is also the checkpoint the live dashboard feed
streams from, so a row here and a run on the Results tab are one experiment.
If the checkpoint is missing, `compare.py` builds it (`sim_feed.py
--checkpoint-only`) before running anything.

The alternative — each rule warming up under itself — was tried first and is
wrong twice over: the rows then compare two different histories, and `slate`
has to pay hours of its own warm-up before a single comparable day is run.

KPIs in the table are over the reporting window, days 90–120: **lots that
completed in the window**, and the cycle time, on-time share and total
tardiness of those lots. Counting only lots *released* after day 90 would
leave a 30-day window nearly empty against a ~35-day cycle time.

The hourly series behind each row (what the Results tab draws and summarises)
is taken by `sim_feed.FeedPlugin` itself, riding the benchmark run with a
null sink: WIP, running, utilisation, trailing-day throughput / cycle time /
on-time / tardiness, decisions and how many the optimizer made, starts, and
the lot-hour split, by the feed's definitions and nobody else's.

### Head-to-head — LVHM, days 90→120 from the shared fifo checkpoint, seed 0, batch=Demand

`compare_SMT2020_LVHM_seed0_120d_w90.json`. Each rule ran as its own
process (`--rules fifo`, `--rules cr`, `--rules slate`) and the files were
merged with `--merge`:

| rule | completed (30 d) | cycle time (d) | on-time % | tardiness (lot·d) | coverage | wall |
|---|---|---|---|---|---|---|
| fifo  | 1,713 | 35.900 | 97.96 | 32.9 | – | 265 s |
| cr    | 1,599 | 36.431 | **99.81** | **2.2** | – | 274 s |
| slate | **1,729** | **35.799** | 98.67 | 7.5 | 47.0% | 2,444 s |

The gate for this configuration: `compare_SMT2020_LVHM_seed0_92d_w90.json`,
`slate-cr` reproduced `cr`'s 42,139 decisions from the checkpoint exactly
(fp `514cf477c555b63c`).

What it says, and what it does not:

- **`slate` completes the most lots with the shortest cycle time**, +16 lots
  (+0.9%) and −0.10 d against `fifo`, +130 lots (+8%) and −0.63 d against
  `cr`. Thirty days is one window and one seed; the `fifo` margin is inside
  what a seed change could move, the `cr` margin probably is not.
- **`cr` still owns due dates.** 99.8% on time and 2.2 lot·days of tardiness
  against `slate`'s 7.5 and `fifo`'s 32.9. The slate's due-date term is in
  the Tier-1 urgency, so either it is too weak against the throughput-shaped
  objective, or a per-cycle assignment blind to sequencing costs exactly what
  adr/0002 predicted. The pressure ablation separates those two and has not
  been run.
- **Coverage 47%**: about half of `slate`'s decisions were the fallback score,
  not the solver. The row is a blend and says so.
- `slate` is **9x the wall clock** of the sort keys; 1,772 s of the 2,444 s
  was inside CP-SAT (40,317 rebuilds).
- **`slate` is not reproducible to the decision.** `fifo` and `cr` re-run
  from the checkpoint to the identical fingerprint; `slate` does not, because
  the 5 ms per-family budget is wall-clock and the incumbent CP-SAT returns
  depends on what else the machine was doing. Two runs of the same command:
  1,726 / 35.661 d / 98.61% / 10.3 and 1,729 / 35.799 d / 98.67% / 7.5.
  That spread — 3 lots, 0.14 d, 2.8 lot·days — is the noise floor for any
  `slate` number here, and it is larger than the `fifo` margin on cycle time.

### Two measurements worth keeping

**A 5 ms per-family budget beats a 50 ms one.** Three simulated hours, LVHM:

| budget | plan time | coverage |
|---|---|---|
| 50 ms | 20.5 s | 56.2% |
| 20 ms | 12.3 s | 58.6% |
| 5 ms  |  6.3 s | 58.8% |

3.3x faster and *slightly better*. A per-family model is ~25 lots against ~12
machines; CP-SAT closes it well inside 5 ms and the rest of the budget goes on
proving optimality nobody collects. This is the decomposition thesis of
adr/0009 confirmed by measurement.

**But note how this sweep was nearly a trap.** It covers three SIMULATED HOURS.
Adopting 5 ms and running 30 simulated days, OR-Tools aborted the process
twenty minutes in:

```
F0000 integer_search.cc:1217] Check failed: heuristics.fixed_search != nullptr
```

A failed CHECK cannot be caught, so the run died and wrote nothing. The cause
was not the budget: `SolveParams::deterministic` set `interleave_search(true)`,
which asks for a `fixed` subsolver that needs a decision strategy this model
never defines. With one worker and a short deadline it trips the check. It is
now gated on `threads > 1`; a single worker is deterministic anyway.

The sweep's numbers were right. The default derived from them shipped a
process-killing bug, because a benchmark short enough to iterate on was short
enough to miss it.

**Planning only for currently-free tools does not work.** `usable_machines` is
the handful awaiting a decision at that instant, so a slate built from it holds
a few tokens and ~94% of decisions fall through. Planning across the whole fab
and serving lookups as tools free — which is what the production dispatcher
does — took coverage from 6.3% to 56.6%.

## Recorded numbers, and their provenance

Everything below came from a Python implementation (`tools/cpsat_bench.py`) that
was **deliberately deleted**, and is SUPERSEDED by
`results/2026-08-29-ortools-linked.txt`. Kept for provenance. Everything below — keeping two implementations of one model is the
dual-implementation problem the port to `src/test_main.cpp` removed. So these
are the claim to be reproduced, not evidence.

```
200 lots x 60 tools, 859 feasible pairs
            assigned    objective     solve   status
greedy            69      6,984,848      0.4ms  GREEDY
cpsat             80      4,584,282   5005.9ms  OPTIMAL
cost reduction vs greedy: +34.4%   lots assigned: +11   violations: both 0
```

Scaling sweep, 5s budget:

| lots | tools | pairs | greedy | cpsat | lift |
|---|---|---|---|---|---|
| 50 | 20 | 205 | 0ms | 2.9s | +44.8% |
| 200 | 50 | 842 | 0ms | 5.0s | +23.2% |
| 800 | 200 | 3,367 | 2ms | 5.0s | +28.5% |

The operational constraint: at 400 lots a 0.25s budget returns no incumbent at
all and the greedy fallback fires. The tactical cycle must allow >=1s of solve
time at this scale. The fallback is load-bearing, not decorative.

## Which scenario

**LVHM, by default, everywhere.** The reasoning, the assumptions it rests on,
and the tests that would overturn it are in [`docs/adr/0001`](../docs/adr/0001-lvhm-default-scenario.md). Read
it before quoting any number as being about "the fab" — it is about one of two
fabs, and section 5 records evidence that already pushes back on the rationale.

## Baseline side

`baselines/pyscfabsim/` reports throughput/cycle-time over SMT2020 HVLM and LVHM
under FIFO and CR, plus a PPO agent. Its metrics are not yet mapped onto the
dispatcher's objective; doing that mapping is step 4, and it is a real modelling
question, not glue code.
