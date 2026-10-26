#!/bin/bash
# Tune qtfw's firing threshold before calling scale 3 the optimiser's opening.
# Seed 0, 15 d, from warmed qp050 checkpoints (same as qtfw_ab).
set -u
REPO=/home/alton/src/fab-optimization/.claude/worktrees/adr-0013-runs
PY=$REPO/baselines/pyscfabsim/.venv/bin/python3
OUT=$REPO/bench/results/qtfw_ab
cd "$REPO" || exit 1
export QTF_LOOKAHEAD=6
QTFW_SLACK_H=4 "$PY" bench/tools/hold_ab.py cqt3r0c 0 off 15 qtfw > "$OUT/cqt3r0c_s0_qtfw_s4.json" 2> "$OUT/cqt3r0c_s0_qtfw_s4.err" &
QTFW_SLACK_H=8 "$PY" bench/tools/hold_ab.py cqt3r0c 0 off 15 qtfw > "$OUT/cqt3r0c_s0_qtfw_s8.json" 2> "$OUT/cqt3r0c_s0_qtfw_s8.err" &
QTFW_SLACK_H=4 QTFW_MAXWAIT_H=12 "$PY" bench/tools/hold_ab.py cqt3r0c 0 off 15 qtfw > "$OUT/cqt3r0c_s0_qtfw_s4m12.json" 2> "$OUT/cqt3r0c_s0_qtfw_s4m12.err" &
QTFW_SLACK_H=4 "$PY" bench/tools/hold_ab.py cqt5r0c 0 off 15 qtfw > "$OUT/cqt5r0c_s0_qtfw_s4.json" 2> "$OUT/cqt5r0c_s0_qtfw_s4.err" &
wait
echo "qtfw_tune end $(date -Is)" >> "$OUT/driver.log"
