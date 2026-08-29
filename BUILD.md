# Build

## Toolchain

`dispatch/` needs a C++20 compiler and `make`.

```bash
sudo apt install build-essential cmake
```

Verified with g++ 13.3.0, GNU Make 4.3, cmake 3.28.3 on Ubuntu 24.04 (WSL2).

## OR-Tools / CP-SAT

Use the prebuilt C++ archive and CMake. Hand-rolled `-I/-L` flags do not work:
the protobuf headers need defines that only `find_package(ortools)` supplies.

```bash
curl -L -o ortools.tgz \
  https://github.com/google/or-tools/releases/download/v9.15/or-tools_amd64_ubuntu-24.04_cpp_v9.15.6755.tar.gz
mkdir -p ~/opt && tar xzf ortools.tgz -C ~/opt

cd dispatch
cmake -S . -B build-ortools -DFAB_HAVE_ORTOOLS=ON \
      -DCMAKE_PREFIX_PATH=~/opt/or-tools_x86_64_Ubuntu-24.04_cpp_v9.15.6755
cmake --build build-ortools -j4

./build-ortools/fabtest            # 56/56, cpsat contract tests now real
./build-ortools/fabtest --bench 5  # CP-SAT vs greedy
```

The plain `make` targets build greedy-only; that is the fallback path and is
worth keeping runnable. CMake is the path for any run whose numbers you intend
to quote.

Other backends: HiGHS (free MILP, reads the LP text `SolverExporter::to_lp()`
already emits) and Gurobi (commercial, needs `GUROBI_HOME` and a license daemon
in the fab zone). Neither is linked here.

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
