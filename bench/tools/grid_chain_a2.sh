#!/bin/bash
# Start A2 (seeds 1,3,4) at 4 jobs once the T3 warm-up driver has ended, so the
# coordinator stays at <= 12 processes (8 A1 + 4 A2). Refuses if any warm-up failed.
set -u
REPO=/home/alton/src/fab-optimization/.claude/worktrees/adr-0013-runs
W=$REPO/bench/results/grid/warmups/driver.log
until grep -q "grid warm-ups end" "$W"; do sleep 60; done
if grep -q "rc=[1-9]" "$W"; then echo "A2 NOT STARTED: a warm-up failed $(date -Is)"; exit 1; fi
echo "warm-ups ended $(date -Is); starting A2"
exec "$REPO/bench/tools/sweep_grid.sh" 4 "qt qtf cr fifo" "5 1 3 8" "1 3 4" 1.00 60
