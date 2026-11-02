#!/bin/bash
# Paired 15-day A/B of the qt batch-tier fix from warmed checkpoints (no hold).
# tier0 must reproduce bench/results/hold_ab/*_off.json exactly.
set -u
REPO=/home/alton/src/fab-optimization/.claude/worktrees/adr-0013-runs
PY=$REPO/baselines/pyscfabsim/.venv/bin/python3
OUT=$REPO/bench/results/batch_tier_ab
mkdir -p "$OUT"
cd "$REPO" || exit 1
for t in cqt5r0c cqtr0c; do for s in 0 2; do for b in 0 1; do
  QT_BATCH_TIER=$b "$PY" bench/tools/hold_ab.py "$t" "$s" off 15 \
      > "$OUT/${t}_s${s}_tier${b}.json" 2> "$OUT/${t}_s${s}_tier${b}.err" &
done; done; done
wait
