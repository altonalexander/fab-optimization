#!/bin/bash
# Fair headroom bound: qtfw with every batch_min = 1, from the qtfw warm-ups.
# Compare with crit_v5_ab/x{3,2}_s{0,2}_qtfw.json (same checkpoints, 15 d).
# If scrap barely moves, the critical range is capacity-limited and no batching
# decision -- rule or optimiser -- can recover it.
set -u
REPO=/home/alton/src/fab-optimization/.claude/worktrees/adr-0013-runs
PY=$REPO/baselines/pyscfabsim/.venv/bin/python3
OUT=$REPO/bench/results/bound_fair
mkdir -p "$OUT"
cd "$REPO" || exit 1
export QTF_LOOKAHEAD=6 QTFW_SLACK_H=8 QT_PROMOTE_FRAC=0.50
for x in 3 2; do for s in 0 2; do
  ck=$(ls $REPO/bench/snapshots/SMT2020_LVHM_seed${s}_qtfw_Demand_day90_cqt${x}r0c_qp050bf6w8*_h270.ckpt | head -1)
  AB_CKPT=$ck "$PY" bench/tools/hold_ab.py cqt${x}r0c $s off 15 qtfw minb1 > "$OUT/x${x}_s${s}_qtfw_minb1.json" 2> "$OUT/x${x}_s${s}_qtfw_minb1.err" &
done; done
wait
echo "bound_fair end $(date -Is)" >> "$OUT/driver.log"
