#!/bin/bash
# Batch fill across window scales, from warmed scrap-on-first checkpoints.
set -u
REPO=/home/alton/src/fab-optimization/.claude/worktrees/adr-0013-runs
PY=$REPO/baselines/pyscfabsim/.venv/bin/python3
OUT=$REPO/bench/results/batch_fill
mkdir -p "$OUT"
cd "$REPO" || exit 1
"$PY" bench/tools/batch_fill_probe.py cqt8r0c 0 3 > "$OUT/x8_s0.json" 2> "$OUT/x8_s0.err" &
"$PY" bench/tools/batch_fill_probe.py cqt5r0c 0 3 > "$OUT/x5_s0.json" 2> "$OUT/x5_s0.err" &
"$PY" bench/tools/batch_fill_probe.py cqtr0c 0 3 > "$OUT/x1_s0.json" 2> "$OUT/x1_s0.err" &
"$PY" bench/tools/batch_fill_probe.py cqtr0c 2 3 > "$OUT/x1_s2.json" 2> "$OUT/x1_s2.err" &
wait
