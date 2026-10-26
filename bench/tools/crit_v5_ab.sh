#!/bin/bash
# crit v5 (hybrid: qtfw unless at-risk groups outnumber free furnaces; family
# model, priced under-min, no holds) vs qtfw s8, on FAIR qtfw warm-ups.
# Scales 3 and 2 (the critical range), seeds 0 and 2, 15 d.
set -u
REPO=/home/alton/src/fab-optimization/.claude/worktrees/adr-0013-runs
PY=$REPO/baselines/pyscfabsim/.venv/bin/python3
OUT=$REPO/bench/results/crit_v5_ab
mkdir -p "$OUT"
cd "$REPO" || exit 1
export QTF_LOOKAHEAD=6 QTFW_SLACK_H=8 QT_PROMOTE_FRAC=0.50
for x in 3 2; do for s in 0 2; do
  ck=$(ls $REPO/bench/snapshots/SMT2020_LVHM_seed${s}_qtfw_Demand_day90_cqt${x}r0c_qp050bf6w8*_h270.ckpt | head -1)
  AB_CKPT=$ck "$PY" bench/tools/hold_ab.py cqt${x}r0c $s off 15 qtfw > "$OUT/x${x}_s${s}_qtfw.json" 2> "$OUT/x${x}_s${s}_qtfw.err" &
  AB_CKPT=$ck CRIT_MODEL=family CRIT_UNDERFILL_W=300 CRIT_HYBRID=1 "$PY" bench/tools/hold_ab.py cqt${x}r0c $s off 15 crit > "$OUT/x${x}_s${s}_v5.json" 2> "$OUT/x${x}_s${s}_v5.err" &
done; done
wait
echo "crit_v5_ab end $(date -Is)" >> "$OUT/driver.log"
