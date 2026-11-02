#!/bin/bash
# Find the scrap band under the testbed's OWN queue-time semantics.
#
# Scrap-on-first (WSC 2020) is stable at every scale we have tried but trades
# stability for material: ~1-2% scrapped at scale 8, 73-76% at scale 1. Between
# them there should be a band where a sort key scraps a meaningful but
# non-catastrophic share -- and that is where "the solver holds windows a sort
# key drops" would show up as SCRAP REDUCTION, a stronger claim than a few
# on-time points because scrap is lost material.
#
# Three promote thresholds per scale, not one. diag_cqt_rework.sh showed that
# detection-only -- which changes no physics at all -- still doubles cycle time
# at scale 1, purely because a threshold tuned at scale 10 chases hopeless
# windows. A single stale threshold would inflate scrap and read as a harder
# fab than it is, which is the mistake the scale sweep already made once.
#
# QT_PROMOTE_FRAC is in the checkpoint key (sim_feed.qt_tuning_key) and the cap
# of 0 is too (`_cqtNr0c`), so no two cells can share a warm-up.
set -u
REPO=/home/alton/src/fab-optimization/.claude/worktrees/adr-0013-runs
PY=$REPO/baselines/pyscfabsim/.venv/bin/python3
OUT=$REPO/bench/results/scrapfirst
mkdir -p "$OUT"

SEEDS="0 1 2 3 4"
SCALES="5 3 2"
FRACS="0.25 0.50 1.00"
JOBS=45

run_cell() {
  local seed=$1 scale=$2 frac=$3
  local tag="s${seed}_x${scale}_qp${frac/./}"
  [ -s "$OUT/${tag}.json" ] && { echo "SKIP $tag"; return 0; }
  cd "$REPO" || return 1
  QT_PROMOTE_FRAC="$frac" "$PY" bench/tools/compare.py \
      --days 180 --warmup-days 90 --warmup-dispatcher qt \
      --seed "$seed" --rules qt \
      --cqt --cqt-scale "$scale" --cqt-max-rework 0 \
      --out "$OUT/${tag}.json" > "$OUT/${tag}.log" 2>&1
  echo "DONE $tag rc=$? $(date -Is)"
}
export -f run_cell
export REPO PY OUT

echo "scrapfirst sweep start $(date -Is) scales=[$SCALES] fracs=[$FRACS] jobs=$JOBS"
for scale in $SCALES; do for frac in $FRACS; do for seed in $SEEDS; do
  echo "$seed $scale $frac"
done; done; done | xargs -P "$JOBS" -n 3 bash -c 'run_cell "$0" "$1" "$2"'
echo "scrapfirst sweep end $(date -Is)"
echo "cells: $(ls "$OUT"/*.json 2>/dev/null | wc -l) / 45"
