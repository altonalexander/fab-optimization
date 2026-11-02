#!/bin/bash
# Append a READY line to agentchats.md (append-only) and chain.log once every
# named checkpoint exists AND its warm-up log reports DONE rc=0 (a checkpoint
# file can appear before it is fully written).
#   grid_ready_flag.sh "MESSAGE" WARMUP_LOG "TAG1 TAG2" CKPT1 [CKPT2 ...]
set -u
REPO=/home/alton/src/fab-optimization/.claude/worktrees/adr-0013-runs
MSG=$1; LOG=$2; TAGS=$3; shift 3
ok() {
  for c in "$@"; do [ -s "$c" ] || return 1; done
  for t in $TAGS; do grep -q "DONE $t rc=0" "$LOG" 2>/dev/null || return 1; done
}
until ok "$@"; do sleep 60; done
now=$(date -u +%FT%TZ)
echo "$now READY $MSG" >> "$REPO/bench/results/grid/chain.log"
printf '\n**%s coordinator (auto)** — %s\n' "$(date -u +'%F %H:%MZ')" "$MSG" >> "$REPO/agentchats.md"
