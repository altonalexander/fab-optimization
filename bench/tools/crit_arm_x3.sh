#!/bin/bash
# crit arm, scale 3, seeds 0 and 2, 60 d, default budget (lead 2026-09-17 03:08Z).
REPO=/home/alton/src/fab-optimization/.claude/worktrees/adr-0013-runs
cd "$REPO" || exit 1
export QTFW_SLACK_H=8
bash bench/tools/sweep_grid.sh 2 crit "3" "0 2" 1.00 60 > bench/results/grid/crit_x3_driver.log 2>&1
