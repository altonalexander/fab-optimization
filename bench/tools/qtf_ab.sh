#!/bin/bash
# Paired 15-day A/B: qtf (feed-the-batch, lookahead 3 and 6) vs qt, from the same
# warmed checkpoints as bench/results/batch_tier_ab (whose tier1 files are the
# qt arm -- same checkpoint, same random state, batch-tier fix on).
set -u
REPO=/home/alton/src/fab-optimization/.claude/worktrees/adr-0013-runs
PY=$REPO/baselines/pyscfabsim/.venv/bin/python3
OUT=$REPO/bench/results/qtf_ab
mkdir -p "$OUT"
cd "$REPO" || exit 1
for t in cqt5r0c cqtr0c; do for s in 0 2; do for k in 3 6; do
  QTF_LOOKAHEAD=$k "$PY" bench/tools/hold_ab.py "$t" "$s" off 15 qtf \
      > "$OUT/${t}_s${s}_k${k}.json" 2> "$OUT/${t}_s${s}_k${k}.err" &
done; done; done
wait
