#!/usr/bin/env bash
# Build libfabslate.so -- the C ABI that bench/tools/slate_rule.py loads.
#
# Goes through CMake rather than a direct g++ line for one reason: OR-Tools'
# cp_model.pb.h needs protobuf's include paths, and only find_package(ortools)
# knows where they are. A hand-rolled `-I$ORTOOLS/include` compiles the headers
# with CpModelProto still forward-declared and fails deep inside solver.hpp.
#
# Falls back to a greedy-only build when OR-Tools is absent. That still
# exercises the whole harness, so slate_rule can be developed on a machine
# without OR-Tools -- but it is NOT a configuration to draw conclusions from,
# and fabslate_solver_available() reports 0 so the Python side can say so.
set -euo pipefail

cd "$(dirname "$0")"

ORTOOLS="${ORTOOLS_ROOT:-$HOME/opt/or-tools_x86_64_Ubuntu-24.04_cpp_v9.15.6755}"
BUILD=build-ortools

if [ -f "$ORTOOLS/include/ortools/sat/cp_model.h" ]; then
  echo "  ortools: $ORTOOLS"
  # ortoolsConfig.cmake does find_dependency(ZLIB) and find_dependency(BZip2),
  # and this box has the runtime .so.1 files but no -dev packages. The OR-Tools
  # distribution bundles both, headers included, so point CMake at those rather
  # than requiring a system install. The existing build-ortools/ tree only
  # works because these paths are already in its CMakeCache -- a fresh
  # configure does not inherit them, which is why this is spelled out.
  cmake -S . -B "$BUILD" -DCMAKE_BUILD_TYPE=Release \
        -DFAB_HAVE_ORTOOLS=ON \
        -DCMAKE_PREFIX_PATH="$ORTOOLS" \
        -Dortools_DIR="$ORTOOLS/lib/cmake/ortools" \
        -DZLIB_INCLUDE_DIR="$ORTOOLS/include" \
        -DZLIB_LIBRARY_RELEASE="$ORTOOLS/lib/libz.so" \
        -DBZIP2_INCLUDE_DIR="$ORTOOLS/include" \
        -DBZIP2_LIBRARY_RELEASE="$ORTOOLS/lib/libbz2.so" >/dev/null
else
  echo "  ortools NOT found at $ORTOOLS -- greedy-only build"
  BUILD=build-greedy
  cmake -S . -B "$BUILD" -DCMAKE_BUILD_TYPE=Release >/dev/null
fi

cmake --build "$BUILD" --target fabslate -j "$(nproc)" >/dev/null
cp "$BUILD/libfabslate.so" libfabslate.so
echo "  built $(pwd)/libfabslate.so"
