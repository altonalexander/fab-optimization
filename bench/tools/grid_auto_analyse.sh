#!/bin/bash
# When the named lanes all log "lane done", re-run analyse_grid.py into
# bench/results/grid/GRID_TABLE_auto.txt and flag it in chain.log + agentchats.md.
# GRID_SUMMARY.txt and the lab note still need a human/agent read of the table.
#   grid_auto_analyse.sh "W1 W2 B E"
set -u
REPO=/home/alton/src/fab-optimization/.claude/worktrees/adr-0013-runs
G=$REPO/bench/results/grid
LANES=$1
done_all() { for l in $LANES; do grep -qE "lane=$l (lane done|STOP)" "$G/chain.log" || return 1; done; }
until done_all; do sleep 120; done
"$REPO/baselines/pyscfabsim/.venv/bin/python3" "$REPO/bench/tools/analyse_grid.py" \
    --json "$G/grid_table_auto.json" > "$G/GRID_TABLE_auto.txt" 2>&1
# Optional paired deltas: PAIR="ARM_A ARM_B" (60 d and 90 d).
if [ -n "${PAIR:-}" ]; then
  for w in 60 90; do
    "$REPO/baselines/pyscfabsim/.venv/bin/python3" "$REPO/bench/tools/grid_pair_delta.py" $PAIR --win $w
  done > "$G/GRID_PAIRED_auto.txt" 2>&1
fi
echo "$(date -u +%FT%TZ) ANALYSED lanes [$LANES] -> GRID_TABLE_auto.txt" >> "$G/chain.log"
printf '\n**%s coordinator (auto)** — lanes [%s] finished; analyse_grid.py re-run -> `bench/results/grid/GRID_TABLE_auto.txt`. GRID_SUMMARY + lab note still to update from it.\n' \
    "$(date -u +'%F %H:%MZ')" "$LANES" >> "$REPO/agentchats.md"
