#!/bin/bash
# Hold-before-entry, warmed, all seeds (docs/notes/2026-09-16-scrap-band.md).
#
# Scrap-on-first, qt at promote 0.50. Scale 5 is the proposed operating point;
# 2 and 1 are where a hold has the most scrap to recover. Two gate settings:
# hold when the exit wait exceeds half the window, or the whole window. The
# no-hold baselines already exist (scrapfirst/*_qp050, cqtdiag/scrapfirst_*_x1).
#
# CQT_HOLD_FRAC is in the checkpoint key (sim_feed.hold_key), so each gate
# setting warms its own fab.
set -u
REPO=/home/alton/src/fab-optimization/.claude/worktrees/adr-0013-runs
PY=$REPO/baselines/pyscfabsim/.venv/bin/python3
OUT=$REPO/bench/results/hold
mkdir -p "$OUT"

SEEDS="0 1 2 3 4"
SCALES="5 2 1"
HOLDS="0.50 1.00"
JOBS=30

run_cell() {
  local seed=$1 scale=$2 hold=$3
  local tag="s${seed}_x${scale}_hq${hold/./}"
  [ -s "$OUT/${tag}.json" ] && { echo "SKIP $tag"; return 0; }
  cd "$REPO" || return 1
  QT_PROMOTE_FRAC=0.50 CQT_HOLD_FRAC="$hold" "$PY" bench/tools/compare.py \
      --days 180 --warmup-days 90 --warmup-dispatcher qt \
      --seed "$seed" --rules qt \
      --cqt --cqt-scale "$scale" --cqt-max-rework 0 \
      --out "$OUT/${tag}.json" > "$OUT/${tag}.log" 2>&1
  echo "DONE $tag rc=$? $(date -Is)"
}
export -f run_cell
export REPO PY OUT

echo "hold sweep start $(date -Is) scales=[$SCALES] holds=[$HOLDS] jobs=$JOBS"
for scale in $SCALES; do for hold in $HOLDS; do for seed in $SEEDS; do
  echo "$seed $scale $hold"
done; done; done | xargs -P "$JOBS" -n 3 bash -c 'run_cell "$0" "$1" "$2"'
echo "hold sweep end $(date -Is)"
echo "cells: $(ls "$OUT"/*.json 2>/dev/null | wc -l) / 30"
