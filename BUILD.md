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

## Third-party notices

The dependency pins above are the input to `THIRD_PARTY_NOTICES.md`, which is
generated, not hand-written. After changing any pin -- the OR-Tools version in
particular -- regenerate it:

```bash
python3 scripts/gen_third_party_notices.py           # writes root + dispatch/legal/
python3 scripts/gen_third_party_notices.py --check   # CI gate: fails if stale
```

It writes two copies on purpose. The container build context is `dispatch/`, so
`dispatch/legal/` is the one the images `COPY`; Apache-2.0 and the other
licences here require the notice to travel with distributed binaries, not just
with the repository.

Adding a dependency with no licence entry is a hard error rather than a silent
omission -- the same rule as the backend table.

## Acceptance gate

```bash
cd dispatch
make test          # expect 56/56
make hsms-test     # two processes, real TCP, real HSMS handshake
make bench         # solver comparison; prints the backend table FIRST
```

`make bench` prints which backends are actually linked before any results, so an
unlinked CP-SAT cannot masquerade as a tie with greedy. It also prints a
`RUN CONFIG` block -- solver version, threads, time limit, gap, stopping
criterion -- which must be quoted alongside any published table. The version
comes from `OrToolsVersionString()` in the linked library, never a literal; with
CP-SAT unlinked it reads `unavailable (not linked)` and the run says so.

## Baseline

```bash
cd baselines/pyscfabsim
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
./reproduce_dispatcher_experiments.sh
```

Its `datasets/` is a symlink into `../../data/smt2020`. If that symlink is
broken, stop — do not point it at a private copy.
