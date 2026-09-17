#!/bin/bash
# A detached lane of grid blocks: wait for one driver log to end, then run each
# block in turn at the lane's job count. Three lanes of 4 jobs keep the
# coordinator at <= 12 processes. Events go to bench/results/grid/chain.log.
#
#   grid_lane.sh NAME WAIT_LOG JOBS "BLOCK" ["BLOCK" ...]
#   BLOCK = 'RULES|SCALES|SEEDS|WINDOW'   (load 1.00; QTFW_SLACK_H from env)
set -u
REPO=/home/alton/src/fab-optimization/.claude/worktrees/adr-0013-runs
G=$REPO/bench/results/grid
NAME=$1; WAIT=$2; JOBS=$3; shift 3
ev() { echo "$(date -u +%FT%TZ) lane=$NAME $*" >> "$G/chain.log"; }
ev "armed, waiting for $(basename "$WAIT")"
until grep -qE "^grid end|grid warm-ups end" "$WAIT" 2>/dev/null; do sleep 60; done
if grep -q "rc=[1-9]" "$WAIT"; then ev "STOP: non-zero rc in $(basename "$WAIT")"; exit 1; fi
ev "RELEASE-READY $(basename "$WAIT") ended; lane takes its $JOBS slots"
i=0
for block in "$@"; do
  i=$((i + 1))
  IFS='|' read -r rules scales seeds win <<< "$block"
  log="$G/lane_${NAME}_b${i}.log"
  # 'WARM:<rule>|SCALES|SEEDS|' builds warm-ups under <rule> instead of cells.
  if [[ $rules == WARM:* ]]; then
    ev "START b$i warm-ups rule=${rules#WARM:} scales=[$scales] seeds=[$seeds] -> $(basename "$log")"
    WARM_RULE=${rules#WARM:} "$REPO/bench/tools/grid_warmups.sh" "$JOBS" "$scales" "$seeds" > "$log" 2>&1
    if grep -q "rc=[1-9]" "$log"; then ev "STOP after b$i: warm-up failure in $(basename "$log")"; exit 1; fi
    ev "END b$i $(grep -c DONE "$log") warm-ups"
    continue
  fi
  ev "START b$i rules=[$rules] scales=[$scales] seeds=[$seeds] win=$win slack=${QTFW_SLACK_H:-2} -> $(basename "$log")"
  "$REPO/bench/tools/sweep_grid.sh" "$JOBS" "$rules" "$scales" "$seeds" 1.00 "$win" > "$log" 2>&1
  if grep -q "rc=[1-9]\|REBUILT-WARMUP" "$log"; then ev "STOP after b$i: failure or warm-up rebuild in $(basename "$log")"; exit 1; fi
  ev "END b$i $(grep -c DONE "$log") cells"
done
ev "lane done; RELEASE $JOBS slots"
