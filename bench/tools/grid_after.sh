#!/bin/bash
# Run a sweep_grid.sh invocation when an earlier driver log says it ended,
# so a queued block inherits that block's slots and the claimed total holds.
#   grid_after.sh <driver.log to wait for> <sweep_grid.sh args...>
set -u
REPO=/home/alton/src/fab-optimization/.claude/worktrees/adr-0013-runs
WAIT=$1; shift
until grep -qE "^grid end|grid warm-ups end" "$WAIT" 2>/dev/null; do sleep 60; done
echo "$WAIT ended $(date -Is); starting sweep_grid.sh $*"
exec "$REPO/bench/tools/sweep_grid.sh" "$@"
