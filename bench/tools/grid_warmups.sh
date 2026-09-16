#!/bin/bash
# T3: rebuild the qt warm-ups under the batch-tier fix (f1b6811) for the grid.
#
# Every published qt checkpoint predates the fix (suffix qp050, no 'b'). The
# grid resumes EVERY rule from one shared qt(b) warm-up per (seed, scale), so
# rows differ only after day 90. Horizon h270 so a 90-day window at
# --starts-scale up to 1.2x fits without a rebuild (180*1.2*1.05 = 227 < 270).
#
#   grid_warmups.sh [JOBS=8] [SCALES="5 1 3 8"] [SEEDS="0 2 1 3 4"]
# Order: seeds 0 and 2 across all scales first, so a first readable grid
# (seeds 0,2) can start before the rest finish. Skips checkpoints on disk
# (looked up with sim_feed.find_ckpt, the same function compare.py uses).
set -u
REPO=/home/alton/src/fab-optimization/.claude/worktrees/adr-0013-runs
PY=$REPO/baselines/pyscfabsim/.venv/bin/python3
OUT=$REPO/bench/results/grid/warmups
JOBS=${1:-8}
SCALES=${2:-"5 1 3 8"}
SEEDS=${3:-"0 2 1 3 4"}
mkdir -p "$OUT"
export QT_PROMOTE_FRAC=0.50 QT_BATCH_TIER=1 SIM_CONTROL_FILE=/dev/null

build() {
  local seed=$1 scale=$2 tag="s${1}_x${2}"
  local ck
  ck=$(cd "$REPO/bench/tools" && "$PY" -c "
import sys; sys.path[:0] = ['$REPO/bench/tools']
import sim_feed
print(sim_feed.find_ckpt('SMT2020_LVHM', $seed, 'qt', 90, 'Demand', 180, None, None, None, True, float($scale), 0) or '')
" 2>/dev/null | tail -1)
  [ -n "$ck" ] && { echo "SKIP $tag $ck"; return 0; }
  cd "$REPO" || return 1
  "$PY" bench/tools/sim_feed.py --dataset SMT2020_LVHM --seed "$seed" \
      --batch-strat Demand --days 270 --dispatcher qt --warmup-days 90 \
      --checkpoint-only --no-store --speed 0 --out /dev/null \
      --cqt --cqt-scale="$scale" --cqt-max-rework=0 \
      > "$OUT/${tag}.log" 2>&1
  echo "DONE $tag rc=$? $(date -Is)"
}
export -f build
export REPO PY OUT

echo "grid warm-ups start $(date -Is) jobs=$JOBS scales=[$SCALES] seeds=[$SEEDS]"
for seed in $SEEDS; do for scale in $SCALES; do echo "$seed $scale"; done; done \
  | xargs -P "$JOBS" -n 2 bash -c 'build "$0" "$1"'
echo "grid warm-ups end $(date -Is)"
