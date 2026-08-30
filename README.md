# fab-optimization

Lot dispatching for a 300mm fab, plus the simulator used to judge whether the
dispatching is any good. The two halves live in one repository for exactly one
reason: **they must read the same data.**

```
dispatch/            the C++ stack (fabdisp) — four zones, sub-ms fast path
baselines/pyscfabsim/  discrete-event fab simulator + PPO agent (vendored, read-only)
data/smt2020/        the shared SMT2020 load — the reason this is one repo
bench/               comparison harness, per-tool probe, committed results
scripts/             dev-up.sh and friends
```

If the dispatcher and the simulator are fed different SMT2020 loads, every
number comparing them is meaningless, and nothing in either program would tell
you. One `data/` directory, symlinked into the baseline, removes that failure
mode. That symlink is load-bearing: if it is ever broken, stop.

## Architecture

```
   ┌──────────── zone 1: equipment ────────────┐
   │  equipment-sim ──HSMS/SECS-II over TCP──> │
   │                        amhs-adapter       │   no browser reaches here
   └────────────────────────┬──────────────────┘
                            │ ZeroMQ
   ┌────────────────────────▼─── zone 2: realtime ──────────────────────┐
   │                                                                    │
   │   ingest ──> FabState ──> planner ──(CP-SAT, 30-60s)──> Slate      │
   │   (single writer)              │                    (atomic swap)  │
   │                                ▼                                   │
   │   move request ──> slate lookup (~200 ns) ──> decision             │
   └────────────────────────┬───────────────────────────────────────────┘
                            │ Kafka
   ┌────────────────────────▼─── zone 3: data ──────────┐
   │   kafka  ·  mes-producer  ·  api (read-only)       │
   └────────────────────────┬───────────────────────────┘
                            │ HTTP (nginx)
   ┌────────────────────────▼─── zone 4: enterprise ────┐
   │   ui — React dashboard                             │
   └────────────────────────────────────────────────────┘
```

The zones are enforced by separate Docker networks, not convention. No service
touches both the tools and a browser. `dispatch/infra/zones.yaml` is the
declaration; `dispatch/infra/verify-zones.sh` checks it.

## The components

**The simulator** — `baselines/pyscfabsim/`. A discrete-event model of the fab:
multi-step routes with rework, setup matrices with minimum-run-lengths,
batching, time- and piece-based PM, breakdowns, due dates. Runs 730 simulated
days and reports cycle time, throughput, on-time %, tardiness, utilization.
Pinned upstream at `ae3d55ef`, read-only — changes belong in `dispatch/` or
`bench/`. See its `UPSTREAM.md`. It plays three roles: fast batch KPI runs for
scenario comparison, paced playback for watching one tool, and (designed, not
built) the environment the dispatcher itself runs inside.

**The dispatch solver** — `dispatch/include/fab/solver.hpp`. A single-period
assignment: one Boolean per feasible (lot, tool) pair, at-most-one tool per lot,
tool capacity, batch-furnace firing bounds, reticle exclusivity. CP-SAT via
OR-Tools when linked, a cost-ordered greedy otherwise. There is **no time
index** — it assigns, it does not sequence. Backends announce whether they are
actually linked, so an unlinked solver cannot masquerade as a tie with greedy.

**The planner and slate** — `planner.hpp`, `slate.hpp`. The planner solves on a
cycle and publishes an immutable `Slate` by atomic pointer swap. The real-time
path never calls a solver: it reads the slate and answers in ~200 ns, falling
back to an alternate tool if the primary went down mid-cycle. This split is the
core design claim of the system.

**The producer** — `producer_sim.hpp`. A load generator, not a fab model: a
hardcoded product mix, random priorities, coin-flip tool downs. It exists so the
ready pool does not grow unbounded while the pipeline is exercised. Anything
that needs fab physics uses the simulator instead.

**The transport** — `transport.hpp` (Kafka), `zmq_transport.hpp` (ZeroMQ),
`hsms.hpp` and `secs2.hpp` (equipment protocol). SECS-II is real; HSMS is a
state machine with the wire codec stubbed; the Kafka bodies are sketched against
librdkafka and not yet compiled.

**The viewer** — two of them today, which is one too many.
`dispatch/ui/` is a React 18 + Vite dashboard reading the Flask API
(`/api/state`, `/api/stream`, `/api/zones`, `/api/scenario/compare`).
`bench/tools/tool_probe.py` is a terminal per-tool view over the simulator.
They share no transport. Unifying them is the next piece of work: have the
simulator publish `DispatchDecision` and `EquipmentState` onto the same stream
the API already speaks, so the React app can grow a tool view instead of a
second UI being maintained.

## Running it

**The dashboard, locally, no Docker:**

```bash
scripts/dev-up.sh            # builds, installs, starts api :8000 and ui :5173
scripts/dev-up.sh --status
scripts/dev-up.sh --stop
```

Run it directly, not through a pipe — see the note in the script. Live panels
stay empty without Kafka; that needs the Docker path below.

**The full four-zone stack:**

```bash
cd dispatch
make infra-up     # docker compose, four networks
make verify       # zone declarations
make reach        # reachability — proves the isolation is real
make logs
make infra-down
```

**The dispatcher on its own:**

```bash
cd dispatch
make test                       # 56/56, greedy-only build
make hsms-test                  # two processes, real TCP, real HSMS handshake
make bench                      # prints which backends are linked FIRST
./build-ortools/fabtest --bench 5   # CP-SAT vs greedy, if built per BUILD.md
```

**The simulator:**

```bash
cd baselines/pyscfabsim
.venv/bin/python3 main.py       # 730-day greedy run, writes KPIs

# per-tool view (from the repo root)
baselines/pyscfabsim/.venv/bin/python3 bench/tools/tool_probe.py --days 30 --top 15
baselines/pyscfabsim/.venv/bin/python3 bench/tools/tool_probe.py \
    --days 30 --tool 970 --follow --speed 3600 --pause-every 20
```

`--follow` re-simulates and paces the render; it is not playback from a stored
stream. Recording one would be ~2.5 GB per 730-day scenario at 22.5k dispatch
events per simulated day, which is an argument for letting Kafka's log be the
recording rather than building a bespoke one.

## Status

Honest accounting, because the numbers here have been wrong before:

- The build works. `make test` is 56/56; OR-Tools v9.15 is linked and CP-SAT
  runs (`bench/results/2026-08-29-ortools-linked.txt`).
- **The dispatcher has never been compared to the simulator on equal terms.**
  The instance generators disagree — 1,625 feasible pairs vs 859 at ~200 lots —
  and the headline +34.4% lift measures 21–30% across the two harnesses. The
  KPI mapping is an open modelling question, not glue. `bench/INTEGRATION.md`
  proposes the fix: run the dispatcher *inside* PySCFabSim.
- `dispatch/README.md`'s "at 400 lots a 1s budget is optimal" is **wrong** on
  the measured build; the threshold is between 1s and 2s. A 1s-tuned cycle would
  silently run greedy while reporting cpsat linked.
- `/api/scenario/compare` cannot succeed as the UI calls it: the UI posts only
  `tool_overrides`, the scenario binary requires a `lots` array, and nothing
  injects one. It returns 500 every time.
- Gurobi and HiGHS are declared backends that fall through to greedy.

See `BUILD.md` for the toolchain and the OR-Tools recipe,
`docs-background-information.md` for the original design rationale.
