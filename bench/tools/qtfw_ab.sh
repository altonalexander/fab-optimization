#!/bin/bash
# Paired 15-day A/B: qtfw (qtf K6 + under-min firing at <2 h window slack) from
# warmed qp050 checkpoints, seed 0, scales 5/3/1; plus qtfK6 at scale 3 (the
# only missing arm). Compare with bound_ab (qt, batch_min=1) and qtf_ab.
set -u
REPO=/home/alton/src/fab-optimization/.claude/worktrees/adr-0013-runs
PY=$REPO/baselines/pyscfabsim/.venv/bin/python3
OUT=$REPO/bench/results/qtfw_ab
mkdir -p "$OUT"
cd "$REPO" || exit 1
export QTF_LOOKAHEAD=6
for t in cqt5r0c cqt3r0c cqtr0c; do
  "$PY" bench/tools/hold_ab.py "$t" 0 off 15 qtfw > "$OUT/${t}_s0_qtfw.json" 2> "$OUT/${t}_s0_qtfw.err" &
done
"$PY" bench/tools/hold_ab.py cqt3r0c 0 off 15 qtf > "$OUT/cqt3r0c_s0_qtfK6.json" 2> "$OUT/cqt3r0c_s0_qtfK6.err" &
wait
echo "qtfw_ab end $(date -Is)" >> "$OUT/driver.log"
