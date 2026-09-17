#!/bin/bash
# crit v4 A/B on the FAIR warm-ups: every arm resumes the qtfw s8 warm-up for
# (scale 3, seed), so neither inherits a qt fab's WIP transient.
#   arms: qtfw s8 | crit v4 u300 fixed | crit v4 u300 load   (seeds 0, 2; 15 d)
# Usage: crit_v4_ab.sh [DAYS=15] [SEEDS="0 2"]
set -u
REPO=/home/alton/src/fab-optimization/.claude/worktrees/adr-0013-runs
PY=$REPO/baselines/pyscfabsim/.venv/bin/python3
OUT=$REPO/bench/results/crit_v4_ab
DAYS=${1:-15}
SEEDS=${2:-"0 2"}
mkdir -p "$OUT"
cd "$REPO" || exit 1
export QTF_LOOKAHEAD=6 QTFW_SLACK_H=8 QT_PROMOTE_FRAC=0.50
for s in $SEEDS; do
  ck=$(ls bench/snapshots/SMT2020_LVHM_seed${s}_qtfw_Demand_day90_cqt3r0c_qp050bf6w8*_h270.ckpt 2>/dev/null | head -1)
  if [ -z "$ck" ]; then echo "missing qtfw warm-up seed $s" >> "$OUT/driver.log"; continue; fi
  AB_CKPT=$ck "$PY" bench/tools/hold_ab.py cqt3r0c $s off $DAYS qtfw > "$OUT/x3_s${s}_qtfw_d${DAYS}.json" 2> "$OUT/x3_s${s}_qtfw_d${DAYS}.err" &
  AB_CKPT=$ck CRIT_MODEL=family CRIT_UNDERFILL_W=300 "$PY" bench/tools/hold_ab.py cqt3r0c $s off $DAYS crit > "$OUT/x3_s${s}_v4u300_d${DAYS}.json" 2> "$OUT/x3_s${s}_v4u300_d${DAYS}.err" &
  AB_CKPT=$ck CRIT_MODEL=family CRIT_UNDERFILL_W=300 CRIT_UNDERFILL_MODE=load "$PY" bench/tools/hold_ab.py cqt3r0c $s off $DAYS crit > "$OUT/x3_s${s}_v4u300load_d${DAYS}.json" 2> "$OUT/x3_s${s}_v4u300load_d${DAYS}.err" &
done
wait
echo "crit_v4_ab end d$DAYS $(date -Is)" >> "$OUT/driver.log"
