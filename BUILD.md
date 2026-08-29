# Build

## Toolchain (not currently installed on this machine)

`dispatch/` needs a C++20 compiler and `make`. Neither is present here, and
installing requires sudo:

```bash
sudo apt install build-essential cmake
```

Optional solver backends, in the order they matter:

```bash
# OR-Tools / CP-SAT — the one that turns the benchmark argument into evidence
#   https://developers.google.com/optimization/install/cpp
# HiGHS — free MILP, reads the LP text SolverExporter::to_lp() already emits
# Gurobi — commercial, needs GUROBI_HOME and a license daemon in the fab zone
```

## Acceptance gate

```bash
cd dispatch
make test          # expect 56/56
make hsms-test     # two processes, real TCP, real HSMS handshake
make bench         # solver comparison; prints the backend table FIRST
```

`make bench` prints which backends are actually linked before any results, so an
unlinked CP-SAT cannot masquerade as a tie with greedy.

## Baseline

```bash
cd baselines/pyscfabsim
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
./reproduce_dispatcher_experiments.sh
```

Its `datasets/` is a symlink into `../../data/smt2020`. If that symlink is
broken, stop — do not point it at a private copy.
