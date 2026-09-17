#!/bin/bash
# End-to-end sanity: one crit v5 grid cell with a 1-day window, through the
# exact sweep_grid.sh -> compare.py path the grid uses. Must exit rc=0 and
# write its JSON; must resume the qtfw warm-up (no rebuild).
REPO=/home/alton/src/fab-optimization/.claude/worktrees/adr-0013-runs
cd "$REPO" || exit 1
export WARM=qtfw QTFW_SLACK_H=8 QTF_LOOKAHEAD=6 CRIT_MODEL=family CRIT_UNDERFILL_W=300 CRIT_HYBRID=1
bash bench/tools/sweep_grid.sh 1 crit "3" "0" 1.00 1
