#!/bin/bash
# T5: the rules-breakdown grid. One compare.py process per cell, every rule
# resumed from the SAME qt(b) warm-up for its (seed, scale). Build those with
# grid_warmups.sh first -- otherwise each compare.py process builds its own,
# racing on the same checkpoint file.
#
#   sweep_grid.sh JOBS "RULES" "SCALES" "SEEDS" ["LOADS"] [WINDOW_DAYS]
#   e.g. sweep_grid.sh 8 "qt qtf cr fifo" "5 1 3 8" "0 2" "1.00" 60
#
# Cell file: bench/results/grid/<rule>_x<scale>_L<load*100>_s<seed>_w<days>.json
# Skips cells already on disk. Claim slots in agentchats.md before launching.
set -u
REPO=/home/alton/src/fab-optimization/.claude/worktrees/adr-0013-runs
PY=$REPO/baselines/pyscfabsim/.venv/bin/python3
OUT=$REPO/bench/results/grid
JOBS=${1:?jobs}; RULES=${2:?rules}; SCALES=${3:?scales}; SEEDS=${4:?seeds}
LOADS=${5:-1.00}; WIN=${6:-60}
mkdir -p "$OUT"
export QT_PROMOTE_FRAC=0.50 QT_BATCH_TIER=1 SIM_CONTROL_FILE=/dev/null
# Lead decision 2026-09-16: qtf runs at K=6; K goes in the cell name (qtfK6).
export QTF_LOOKAHEAD=${QTF_LOOKAHEAD:-6}
export QTFW_SLACK_H=${QTFW_SLACK_H:-2}
# crit v3 needs CRIT_UNDERFILL_W set (unset = no priced under-min batches), so
# pin it rather than let the name say U300 while the run says none.
export CRIT_UNDERFILL_W=${CRIT_UNDERFILL_W:-300} CRIT_BUDGET_S=${CRIT_BUDGET_S:-2} CRIT_PLAN_S=${CRIT_PLAN_S:-1800}
# Any other CRIT_* knob would change crit without changing the cell name.
if env | grep -qE '^CRIT_(HORIZON_S|SLOTS|LOOKAHEAD|PAD_S|HOLD_MAX_S|FAMILIES)='; then
  echo "refusing: a CRIT_* knob outside the cell name is set" >&2; exit 2
fi

cell() {
  local rule=$1 scale=$2 seed=$3 load=$4
  local L; L=$(awk -v l="$load" 'BEGIN{printf "%03d", l*100 + 0.5}')
  local rtag=$rule
  [ "$rule" = qtf ] && rtag="qtfK${QTF_LOOKAHEAD}"
  # qtfw = qtf + fire an underfilled batch when a member has < QTFW_SLACK_H of
  # window left. Fires below the dataset's batch minimum: a DECLARED arm.
  [ "$rule" = qtfw ] && rtag="qtfwK${QTF_LOOKAHEAD}s${QTFW_SLACK_H}${QTFW_MAXWAIT_H:+m$QTFW_MAXWAIT_H}"
  # crit = CP-SAT critical-section scheduler (qtfw fallback). Every knob that
  # changes its decisions goes in the name, defaults included, so a budget-cut
  # cell and a default cell can never share a file.
  [ "$rule" = crit ] && rtag="critK${QTF_LOOKAHEAD}s${QTFW_SLACK_H}U${CRIT_UNDERFILL_W:-300}B${CRIT_BUDGET_S:-2}P${CRIT_PLAN_S:-1800}"
  local tag="${rtag}_x${scale}_L${L}_s${seed}_w${WIN}"
  [ -s "$OUT/${tag}.json" ] && { echo "SKIP $tag"; return 0; }
  cd "$REPO" || return 1
  "$PY" bench/tools/compare.py --days $((90 + WIN)) --warmup-days 90 \
      --warmup-dispatcher qt --seed "$seed" --rules "$rule" \
      --cqt --cqt-scale "$scale" --cqt-max-rework 0 --starts-scale "$load" \
      --out "$OUT/${tag}.json" > "$OUT/${tag}.log" 2>&1
  local rc=$?
  # A cell must resume a T3 warm-up, never build one (racing builds).
  grep -q "building it" "$OUT/${tag}.log" && echo "REBUILT-WARMUP $tag" >&2
  echo "DONE $tag rc=$rc $(date -Is)"
}
export -f cell
export REPO PY OUT WIN

echo "grid start $(date -Is) jobs=$JOBS rules=[$RULES] scales=[$SCALES] seeds=[$SEEDS] loads=[$LOADS] win=$WIN"
# seed-major order: every (rule, scale) gets its first seed before any gets a second
for seed in $SEEDS; do for scale in $SCALES; do for load in $LOADS; do for rule in $RULES; do
  echo "$rule $scale $seed $load"
done; done; done; done | xargs -P "$JOBS" -n 4 bash -c 'cell "$0" "$1" "$2" "$3"'
echo "grid end $(date -Is)"
