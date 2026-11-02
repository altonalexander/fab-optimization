#!/bin/bash
# crit v5 grid arm (relaunch after the rule.stats() crash, 2026-09-17 11:25Z):
#   15 x 60 d  (scales 3 2 4 x seeds 0 2 1 3 4), then 2 x 90 d (scale 3, seeds 0 2).
# Paired with qtfwK6s8Wqtfw cells. Analysis written at the end.
REPO=/home/alton/src/fab-optimization/.claude/worktrees/adr-0013-runs
cd "$REPO" || exit 1
export WARM=qtfw QTFW_SLACK_H=8 QTF_LOOKAHEAD=6 CRIT_MODEL=family CRIT_UNDERFILL_W=300 CRIT_HYBRID=1
G=bench/results/grid
bash bench/tools/sweep_grid.sh 15 crit "3 2 4" "0 2 1 3 4" 1.00 60 > $G/v5_w60_driver.log 2>&1
bash bench/tools/sweep_grid.sh 2 crit "3" "0 2" 1.00 90 > $G/v5_w90_driver.log 2>&1
(cd bench/tools && ../../baselines/pyscfabsim/.venv/bin/python3 analyse_grid.py) > $G/GRID_TABLE_auto.txt 2>&1
(cd bench/tools && ../../baselines/pyscfabsim/.venv/bin/python3 grid_pair_delta.py critv5K6s8U300B2P1800Wqtfw qtfwK6s8Wqtfw --win 60
 ../../baselines/pyscfabsim/.venv/bin/python3 grid_pair_delta.py critv5K6s8U300B2P1800Wqtfw qtfwK6s8Wqtfw --win 90 --scales 3) > $G/GRID_PAIRED_auto.txt 2>&1
echo "v5 grid end $(date -Is)" >> $G/v5_w60_driver.log
