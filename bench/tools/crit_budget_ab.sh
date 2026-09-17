#!/bin/bash
# crit v3 speed + weight A/B (15 d, warmed qp050 checkpoints):
#   budget cut (1 s solves, replan hourly) at scale 3 seeds 0,2 -- adopt if >=2x
#   faster and scrap share within ~1 pt of the defaults (coordinator's rule);
#   underfill weight 600 at scales 5 and 3 (seed 0) -- is 300 too cheap at 5?
set -u
REPO=/home/alton/src/fab-optimization/.claude/worktrees/adr-0013-runs
PY=$REPO/baselines/pyscfabsim/.venv/bin/python3
OUT=$REPO/bench/results/crit_ab
cd "$REPO" || exit 1
export QTF_LOOKAHEAD=6 QTFW_SLACK_H=8
for s in 0 2; do
  CRIT_UNDERFILL_W=300 CRIT_BUDGET_S=1 CRIT_PLAN_S=3600 "$PY" bench/tools/hold_ab.py cqt3r0c $s off 15 crit \
      > "$OUT/cqt3r0c_s${s}_v3u300B1P3600.json" 2> "$OUT/cqt3r0c_s${s}_v3u300B1P3600.err" &
done
CRIT_UNDERFILL_W=600 "$PY" bench/tools/hold_ab.py cqt5r0c 0 off 15 crit > "$OUT/cqt5r0c_s0_v3u600.json" 2> "$OUT/cqt5r0c_s0_v3u600.err" &
CRIT_UNDERFILL_W=600 "$PY" bench/tools/hold_ab.py cqt3r0c 0 off 15 crit > "$OUT/cqt3r0c_s0_v3u600.json" 2> "$OUT/cqt3r0c_s0_v3u600.err" &
wait
echo "crit_budget_ab end $(date -Is)" >> "$OUT/driver.log"
