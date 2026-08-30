# fab-optimization

Lot dispatching for a 300mm fab, plus the simulator used to judge whether the
dispatching is any good. The two halves live in one repository for exactly one
reason: **they must read the same data.**

```
dispatch/            the C++ stack (fabdisp) — four zones, sub-ms fast path
baselines/pyscfabsim/  discrete-event fab simulator + PPO agent (vendored, read-only)
data/smt2020/        the shared SMT2020 load — the reason this is one repo
                     (LVHM is the standard scenario; see docs/adr/0001)
bench/               comparison harness, per-tool probe, committed results
docs/adr/            why things are the way they are, and what would change them
scripts/             dev-up.sh and friends
```

Start with [`docs/adr/0000`](docs/adr/0000-motivation-scope-and-boundaries.md)
for what this project is for and, more usefully, what it is not.

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
   │   kafka · postgres · api (read-only)               │
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

**The stores** — two, with different jobs. **Kafka** holds live state: the
compacted `fab.lot.state` / `fab.tool.state` topics are the keyed,
restart-survivable record the dashboard bootstraps from, which is what solves
cold start (see below). **Postgres** (`infra/postgres-init.sql`) is the *run*
store — runs, KPIs and per-tool outcomes, for comparing dispatchers across
seeds and scenarios. It deliberately holds no live fab state; giving the same
fact two homes is how they drift.

**The transport** — `transport.hpp` (Kafka), `zmq_transport.hpp` (ZeroMQ),
`hsms.hpp` and `secs2.hpp` (equipment protocol). SECS-II is real; HSMS is a
state machine with the wire codec stubbed; the Kafka bodies are sketched against
librdkafka and not yet compiled.

**The viewer** — one of them now. `dispatch/ui/` is a React 18 + Vite
dashboard reading the Flask API (`/api/state`, `/api/stream`, `/api/zones`,
`/api/scenario/compare`). `bench/tools/tool_probe.py` used to carry a second
one: a `--follow` mode that drew its own terminal view of a tool with its own
pacing and breakpoints, over a private copy of the run loop. That is gone. The
probe is now headless-only — run the window, report the time budget — and both
it and `sim_feed.py` drive the simulator through `bench/tools/sim_runner.py`,
so a measured run and a published run are the same run rather than two.

What remains of the unification is the transport: the probe still computes the
per-tool time budget in-process instead of publishing `EquipmentState` onto the
stream the API already speaks. Once it does, the React tool view can show the
busy/setup/pm/down/blocked/starved split that today only the terminal has.

## Cold start

The dashboard is a **mirror**: it holds no state of its own and rebuilds the
fab from the event stream. That is the right design — it means the dashboard
can die, restart, or be opened for the first time mid-shift without anyone
coordinating — but on its own it cannot answer *"what is in the fab right
now?"* at the moment it connects.

It learns a lot exists only when that lot next **moves**. A lot sitting in a
litho queue announces nothing for hours of simulated time, and the ~2,000 lots
the simulator loads from `WIP.txt` are never *released* at all, so they emit
nothing until they happen to dispatch. A freshly started mirror therefore
under-reports WIP for as long as it takes every lot to touch a tool — and it
under-reports it *silently*, which is worse, because a low number looks like a
quiet fab rather than a blind observer.

Replaying history does not fix it. A year is ~15M events, minutes of consumer
time on every restart, and the answer you want is one number per lot, not the
path it took to get there.

**The fix is snapshot + delta.** The producer states the position of every lot
and tool once, as keyed records on a **compacted** Kafka topic, and streams
changes from there. Compaction is what makes this cheap: the log keeps exactly
one record per live key, so a consumer reading from the very beginning reads
*the fab*, not its history. `fab.lot.state` and `fab.tool.state` exist for
this and nothing else.

Because a discrete-event simulator cannot *start* at day 90 — it has to
simulate there, at roughly 3 minutes of CPU per 30 simulated days — the
snapshot is cached to `bench/snapshots/`, keyed by dataset, seed, dispatcher,
batch strategy and day. Building it takes minutes; replaying it takes about
two seconds.

```bash
# once — simulate 90 days silently, snapshot, then stream live from there
baselines/pyscfabsim/.venv/bin/python3 bench/tools/sim_feed.py \
    --days 93 --warmup-days 90 --speed 10

# afterwards — republish the cached snapshot, no simulation
baselines/pyscfabsim/.venv/bin/python3 bench/tools/sim_feed.py \
    --warmup-days 90 --snapshot-only
```

The API applies the snapshot before tailing events, so the dashboard is
populated the moment it starts rather than filling in over the next hour.

## Running it

**The dashboard.** The simulator runs in two modes — the same run loop
(`bench/tools/sim_runner.py`) with a different plugin riding it:

*Mode 1 — headless.* No broker, no feed, no pacing, as fast as possible.
This is what you use for KPIs and parameter tuning:

```bash
baselines/pyscfabsim/.venv/bin/python3 bench/tools/tool_probe.py --days 30 --top 15
```

*Mode 2 — producer.* The simulator emits events the dashboard consumes, paced,
so you can watch near-realtime:

```bash
# terminal 1 — the broker (dev override publishes a host listener)
cd dispatch/infra
docker compose -f docker-compose.yml -f docker-compose.dev.yml \
    --profile data up -d kafka kafka-init

# terminal 2 — api :8000 and ui :5173, consuming from Kafka
cd dispatch && DEMO_LOTS=1 KAFKA_BROKERS=localhost:29092 make api
cd dispatch/ui && npm run dev

# terminal 3 — the simulator as producer: every tool, ten times realtime
baselines/pyscfabsim/.venv/bin/python3 bench/tools/sim_feed.py \
    --days 3 --speed 10
```

Then open http://localhost:5173/.

`--speed` is sim-seconds per wall-second: `1` is realtime, `10` is ten times
realtime (a simulated day takes ~2.4 hours), `0` is unpaced. At `--speed 10`
the whole fab emits about **6 events/second** — the broker and the dashboard
absorb that without effort, so there is no reason to filter with
`--tool-prefix` unless you want to.

Ctrl-C the producer to pause; rerun it to resume. The API consumer starts at
`latest`, so the dashboard is empty until something is produced —
`DEMO_LOTS=1` is what keeps the scenario button working meanwhile.

**Tests:**

```bash
cd dispatch && make test        # 56/56, the C++ suite
scripts/smoke.sh                # 16 assertions over the API, producer, floorplan
python3 bench/tools/t_sim_runner.py   # the shared dispatch loop, any interpreter
```

`smoke.sh` runs on :8111 so it can stand beside a running dev API. Every
assertion in it corresponds to a bug that actually shipped.

`t_sim_runner.py` is the one test here that needs nothing — no venv, no
dataset, no broker. It fakes the instance to pin the loop in
`bench/tools/sim_runner.py`, which both `tool_probe.py` and `sim_feed.py` run
on, so a break there would take out the measurements and the dashboard feed
together. `smoke.sh` runs it first for that reason.

**The full four-zone stack:**

```bash
cd dispatch
make infra-up     # docker compose, four networks

# or one zone at a time — profiles: equipment · realtime · data · enterprise
cd infra && docker compose --profile data up -d
# containers are named fab-<zone>-<service>, e.g. fab-data-kafka

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
    --days 30 --tool 970 --tail 40
```

The probe is headless: it runs the window as fast as it can and reports. To
watch a run unfold, feed the dashboard with `bench/tools/sim_feed.py` and use
its playback controls — that path pages through Kafka's log, so nothing has to
re-simulate to redraw. Recording a bespoke stream instead would be ~2.5 GB per
730-day scenario at 22.5k dispatch events per simulated day, which is the
argument for letting the log be the recording.

## Status

Honest accounting, because the numbers here have been wrong before:

- The build works. `make test` is 56/56; OR-Tools v9.15 is linked and CP-SAT
  runs (`bench/results/2026-08-29-ortools-linked.txt`).
- **The dispatcher has never been compared to the simulator on equal terms.**
  The instance generators disagree — 1,625 feasible pairs vs 859 at ~200 lots —
  and the headline +34.4% lift measures 21–30% across the two harnesses. The
  KPI mapping is an open modelling question, not glue. [`docs/adr/0002`](docs/adr/0002-dispatcher-inside-pyscfabsim.md)
  proposes the fix: run the dispatcher *inside* PySCFabSim.
- The tactical cycle needs **≥2s of solve time at 400 lots**, not the ≥1s once
  stated. A 1s-tuned cycle runs greedy on every tick while the backend table
  honestly reports `cpsat linked` — no error, no warning, no CP-SAT. Measured
  and corrected in `dispatch/README.md`; re-measure per deployment rather than
  inheriting the number.
- The producer is `bench/tools/sim_feed.py`, which publishes to Kafka. It
  still has a hidden `--out` that writes JSONL, kept only so `scripts/smoke.sh`
  can run with no Docker and no broker. There was never a C++ `mes_producer`: `Dockerfile.simulator` built it
  from a source file that does not exist and hid the failure with `|| true`.
  That build step and the broken compose service are gone.
- Gurobi and HiGHS are declared backends that fall through to greedy.
- Kafka now runs. `apache/kafka:3.9.0` could not start at all — it died at its
  storage-format step with `advertised.listeners cannot use the nonroutable
  meta-address 0.0.0.0`, reproducible on a bare `docker run` with a fully
  routable `KAFKA_ADVERTISED_LISTENERS`, so it was the image's own env
  handling. Pinned to `confluentinc/cp-kafka:7.7.1`, which accepts the same
  environment unchanged. Broker healthy, all four topics created, and the real
  wire format round trips producer → broker → consumer on `data-net`.
- **Reaching that broker from the host is unverified.** `docker-compose.dev.yml`
  publishes a second listener for host-side producers, but under Docker Desktop
  + WSL2 here the binding never materialises (a plain `docker run -p` does
  publish, so it is compose-specific to this setup). Run the producer inside
  the data zone — the production shape.

See `BUILD.md` for the toolchain and the OR-Tools recipe.

## Credits

This project stands on two pieces of work by other people. If you use it,
credit them too.

### PySCFabSim — the baseline simulator

The Python simulator in `baselines/pyscfabsim/` is **not our work**. It is
[PySCFabSim](https://github.com/prosysscience/PySCFabSim-release) by the
Research Group Production Systems, MIT-licensed, vendored here read-only so the
C++ dispatcher has a pinned, reproducible baseline to be measured against.
Every number this project reports as a comparison is a number PySCFabSim
produced.

It is vendored via the fork
[david-dd/PySCFabSim-revised](https://github.com/david-dd/PySCFabSim-revised)
at commit `ae3d55ef`, and **has been modified** — seven deviations, documented
in `baselines/pyscfabsim/UPSTREAM.md`. See `baselines/pyscfabsim/README.md` for
the details and `baselines/pyscfabsim/LICENSE` for the MIT terms, which must be
retained in any redistribution.

### Google OR-Tools — the solver

The tactical layer is a CP-SAT model solved with
[Google OR-Tools](https://github.com/google/or-tools) (Apache-2.0), pinned at
**v9.15.6755**. **We did not build a solver.** The contribution here is the
decomposition and the reformulation — priority ranking rather than scheduling,
tool-group rather than per-tool granularity, a four-hour horizon — and CP-SAT
does the search underneath it. Keeping that line visible is the point of this
section.

> Perron, L. and Furnon, V. *OR-Tools*, Google.
> <https://developers.google.com/optimization/>

If you are describing CP-SAT's *behaviour* rather than just noting the
dependency, cite the solver paper as well — Perron, Didier and Gay on the
CP-SAT-LP solver (CP 2023, LIPIcs). Check the exact bibliographic entry against
the proceedings before you publish it; it is not verified here.

OR-Tools is a large dependency that bundles SCIP, SoPlex, the COIN-OR solvers,
abseil and protobuf, each with its own licence. See
[`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md).

### SMT2020 — the dataset

Both the dispatcher and the baseline read the
[SMT2020 Semiconductor Manufacturing Testbed](https://p2schedgen.fernuni-hagen.de/index.php/downloads/simulation),
distributed by FernUniversität in Hagen under its own terms. It is a separate
work from this project and from PySCFabSim. If you publish results derived from
it, cite:

> Kopp, D., Hassoun, M., Kalir, A., & Mönch, L. (2020). SMT2020 — A
> Semiconductor Manufacturing Testbed. *IEEE Transactions on Semiconductor
> Manufacturing.* doi:10.1109/TSM.2020.3001933

This project standardises on the **LVHM** scenario; HVLM works when passed
explicitly. See `docs/adr/0001-lvhm-default-scenario.md`.

The copy in `data/smt2020/` is redistributed here with attribution. It
carries **no licence of its own** — the distribution states no terms at
all — so read [`data/smt2020/PROVENANCE.md`](data/smt2020/PROVENANCE.md)
before redistributing it further.

## Citing this work

Machine-readable metadata is in [`CITATION.cff`](CITATION.cff) — GitHub renders
it as a **Cite this repository** button, and it lists the dependencies below as
formal references.

```bibtex
@software{alexander_fab_optimization,
  author  = {Alexander, Alton},
  title   = {{fab-optimization}: predictive lot dispatching for a
             300mm semiconductor fab},
  year    = {2026},
  license = {Apache-2.0},
  url     = {https://github.com/altonalexander/fab-optimization}
}
```

If you publish results, cite the two works underneath this one as well. They
are not incidental: PySCFabSim produced every baseline number reported here,
and SMT2020 is the load both it and the dispatcher read.

```bibtex
@article{kopp2020smt2020,
  author  = {Kopp, Denny and Hassoun, Michael and Kalir, Adar
             and M\"{o}nch, Lars},
  title   = {{SMT2020} --- A Semiconductor Manufacturing Testbed},
  journal = {IEEE Transactions on Semiconductor Manufacturing},
  year    = {2020},
  doi     = {10.1109/TSM.2020.3001933}
}

@software{perron_ortools,
  author  = {Perron, Laurent and Furnon, Vincent},
  title   = {{OR-Tools}},
  version = {9.15.6755},
  url     = {https://developers.google.com/optimization/}
}

@software{pyscfabsim,
  title  = {{PySCFabSim}},
  note   = {Research Group Production Systems. MIT licence},
  url    = {https://github.com/prosysscience/PySCFabSim-release}
}
```

Quote the `RUN CONFIG` block (below) with any benchmark number, and state the
OR-Tools version — an unversioned CP-SAT result is not reproducible.

There is no DOI for this repository. If you need a citable, archived version,
mint one with [Zenodo](https://zenodo.org/), which snapshots a GitHub release
and issues a DOI; add it to `CITATION.cff` as a `doi:` field afterwards.

## Quoting benchmark numbers

CP-SAT performance changes between OR-Tools releases, so a solver number
without its version is not reproducible. `make bench` therefore prints a
`RUN CONFIG` block before any results:

```
== RUN CONFIG ==
  cpsat version    9.15.6755
  threads          8
  time limit       1 s per solve
  relative gap     0.02
  deterministic    yes
  stopping         first of: proven optimal, gap <= 0.02, or time limit
```

Quote the whole block alongside any table you publish. The version is read from
the linked library via `OrToolsVersionString()` — it is never a compiled-in
literal, so it cannot drift from the binary that produced the numbers.

The same no-silent-fallback rule that governs the backend table governs this:
in a build without CP-SAT linked, the version reads `unavailable (not linked)`
and the run prints an explicit refusal. It is never defaulted to a
plausible-looking value.

## Third-party software

This project links against, bundles, and in one case vendors other people's
work. [`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md) lists every component
with its version, copyright and licence; full texts are in
[`licenses/`](licenses/). Both must travel with any distribution of this
software — source, binaries, or container images.

It is generated from the dependency manifests, not hand-maintained:

```bash
python3 scripts/gen_third_party_notices.py           # regenerate
python3 scripts/gen_third_party_notices.py --check   # CI: fail if stale
```

Adding a dependency without recording its licence is a hard error, by design.

## License

This project is licensed under the **Apache License 2.0** — see `LICENSE`.

You may use, modify, and distribute it, including commercially, provided you
retain the copyright notice, state your changes, and pass along the `NOTICE`
file. It also grants you an explicit patent licence from the contributors.

The Apache-2.0 licence covers this project's own code. It does **not** cover
`baselines/pyscfabsim/` (MIT, see above), the SMT2020 dataset (separate
terms), or any of the libraries it links against. `NOTICE` and
`THIRD_PARTY_NOTICES.md` list all third-party components.
