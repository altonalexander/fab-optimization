#!/bin/bash
# Paired 15-day A/B: crit (CP-SAT critical-section scheduler + qtf K6 elsewhere)
# vs qtf K6 (bench/results/qtf_ab/*_k6.json) and qt (batch_tier_ab/*tier1),
# all from the same warmed qp050 checkpoints.
set -u
REPO=/home/alton/src/fab-optimization/.claude/worktrees/adr-0013-runs
PY=$REPO/baselines/pyscfabsim/.venv/bin/python3
OUT=$REPO/bench/results/crit_ab
TAG=${1:-v1}
mkdir -p "$OUT"
cd "$REPO" || exit 1
export QTF_LOOKAHEAD=6
# extra env (e.g. CRIT_HOLD_MAX_S=0 for the no-hold ablation) passes through
for t in cqt5r0c cqtr0c; do for s in 0 2; do
  "$PY" bench/tools/hold_ab.py "$t" "$s" off 15 crit \
      > "$OUT/${t}_s${s}_${TAG}.json" 2> "$OUT/${t}_s${s}_${TAG}.err" &
done; done
wait
