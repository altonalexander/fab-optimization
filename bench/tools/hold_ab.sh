#!/bin/bash
# 12 paired 15-day A/B runs of hold-before-entry from warmed no-hold checkpoints.
set -u
REPO=/home/alton/src/fab-optimization/.claude/worktrees/adr-0013-runs
PY=$REPO/baselines/pyscfabsim/.venv/bin/python3
OUT=$REPO/bench/results/hold_ab
mkdir -p "$OUT"
cd "$REPO" || exit 1
for t in cqt5r0c cqtr0c; do for s in 0 2; do for h in off 0.5 1.0; do
  "$PY" bench/tools/hold_ab.py "$t" "$s" "$h" 15 > "$OUT/${t}_s${s}_${h}.json" 2> "$OUT/${t}_s${s}_${h}.err" &
done; done; done
wait
cat "$OUT"/*.json
