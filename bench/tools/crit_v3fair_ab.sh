#!/bin/bash
# Is crit v3's scale-3 win real on FAIR (qtfw-warmed) checkpoints?  And do holds
# sink v4?  Arms: crit v3 u300 (tool model) | crit v4 u300 holds off; seeds 0,2; 15 d.
set -u
REPO=/home/alton/src/fab-optimization/.claude/worktrees/adr-0013-runs
PY=$REPO/baselines/pyscfabsim/.venv/bin/python3
OUT=$REPO/bench/results/crit_v4_ab
cd "$REPO" || exit 1
export QTF_LOOKAHEAD=6 QTFW_SLACK_H=8 QT_PROMOTE_FRAC=0.50
for s in 0 2; do
  ck=$(ls $REPO/bench/snapshots/SMT2020_LVHM_seed${s}_qtfw_Demand_day90_cqt3r0c_qp050bf6w8*_h270.ckpt | head -1)
  AB_CKPT=$ck CRIT_UNDERFILL_W=300 "$PY" bench/tools/hold_ab.py cqt3r0c $s off 15 crit > "$OUT/x3_s${s}_v3u300_d15.json" 2> "$OUT/x3_s${s}_v3u300_d15.err" &
  AB_CKPT=$ck CRIT_MODEL=family CRIT_UNDERFILL_W=300 CRIT_HOLD_MAX_S=0 "$PY" bench/tools/hold_ab.py cqt3r0c $s off 15 crit > "$OUT/x3_s${s}_v4u300nohold_d15.json" 2> "$OUT/x3_s${s}_v4u300nohold_d15.err" &
done
wait
echo "crit_v3fair_ab end $(date -Is)" >> "$OUT/driver.log"
