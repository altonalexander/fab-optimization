#!/bin/bash
# Batch-min bound: qt from warmed qp050 checkpoints with every batch_min = 1.
set -u
REPO=/home/alton/src/fab-optimization/.claude/worktrees/adr-0013-runs
PY=$REPO/baselines/pyscfabsim/.venv/bin/python3
OUT=$REPO/bench/results/bound_ab
mkdir -p "$OUT"
cd "$REPO" || exit 1
"$PY" bench/tools/hold_ab.py cqt3r0c 0 off 15 qt > "$OUT/x3_s0_base.json" 2> "$OUT/x3_s0_base.err" &
"$PY" bench/tools/hold_ab.py cqt3r0c 0 off 15 qt minb1 > "$OUT/x3_s0_minb1.json" 2> "$OUT/x3_s0_minb1.err" &
"$PY" bench/tools/hold_ab.py cqt5r0c 0 off 15 qt minb1 > "$OUT/x5_s0_minb1.json" 2> "$OUT/x5_s0_minb1.err" &
"$PY" bench/tools/hold_ab.py cqtr0c 0 off 15 qt minb1 > "$OUT/x1_s0_minb1.json" 2> "$OUT/x1_s0_minb1.err" &
wait
echo "bound_ab end $(date -Is)" >> "$OUT/driver.log"
