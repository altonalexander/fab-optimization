# fab-optimization

Lot dispatching for a 300mm fab, in two halves that exist in one repository for
exactly one reason: **they must read the same data.**

```
dispatch/            the C++ stack (fabdisp) — four zones, sub-ms fast path
baselines/
  pyscfabsim/        vendored discrete-event fab simulator + PPO agent (read-only)
data/smt2020/        the shared SMT2020 load — the reason this is one repo
bench/               comparison harness and committed results
```

If the dispatcher and the baseline are fed different SMT2020 loads, every number
comparing them is meaningless, and nothing in either program would tell you.
One `data/` directory, symlinked into the baseline, removes that failure mode.

## The system

`dispatch/` is a multi-horizon dispatcher. A strategic/tactical planner runs a
MILP or CP-SAT solve on a 30–60s cycle and publishes an immutable `Slate`; the
real-time path never calls a solver, it reads the slate through an atomic
pointer swap and answers in ~200ns. Four network zones — equipment (HSMS/
SECS-II over real TCP), realtime (ZeroMQ), data (Kafka), enterprise (read-only
Flask API + React dashboard) — are enforced by Docker networks, and no service
touches both the tools and a browser.

See `dispatch/README.md` for the architecture and `dispatch/READMEinfra.md` for
the zone topology. `docs-background-information.md` is the original design
rationale (push/pull horizons, system dynamics).

`baselines/pyscfabsim/` is a pinned fork of PySCFabSim with FIFO/CR greedy rules
and a PPO agent over the same benchmark. It is a **baseline to beat**, not a
component of the dispatcher. See its `UPSTREAM.md`.

## Status

Restoration of the flattened source tree is complete and is the first commit.
The build has **not been verified on this machine** — there is no C++ toolchain
installed here (no g++/clang/make/cmake). `make test` should report 56/56; that
claim is inherited from the previous run, not reproduced. See `BUILD.md`.

The next commit is meant to be `-DFAB_HAVE_ORTOOLS`: the C++ CP-SAT path
compiles but has never been linked or run, and every benchmark number in
`dispatch/README.md` came from an implementation that has since been deleted. If
the C++ does not reproduce the +34% lift over greedy, the formulation is wrong
and those numbers are wrong with it.
