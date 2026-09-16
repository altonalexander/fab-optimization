#!/bin/bash
# qtfw threshold, round 2 (seed 0, 15 d), plus the first crit v3 cell.
set -u
REPO=/home/alton/src/fab-optimization/.claude/worktrees/adr-0013-runs
PY=$REPO/baselines/pyscfabsim/.venv/bin/python3
OUT=$REPO/bench/results/qtfw_ab
COUT=$REPO/bench/results/crit_ab
cd "$REPO" || exit 1
export QTF_LOOKAHEAD=6
QTFW_SLACK_H=8  "$PY" bench/tools/hold_ab.py cqt5r0c 0 off 15 qtfw > "$OUT/cqt5r0c_s0_qtfw_s8.json" 2> "$OUT/cqt5r0c_s0_qtfw_s8.err" &
QTFW_SLACK_H=8  "$PY" bench/tools/hold_ab.py cqtr0c 0 off 15 qtfw > "$OUT/cqtr0c_s0_qtfw_s8.json" 2> "$OUT/cqtr0c_s0_qtfw_s8.err" &
QTFW_SLACK_H=16 "$PY" bench/tools/hold_ab.py cqt3r0c 0 off 15 qtfw > "$OUT/cqt3r0c_s0_qtfw_s16.json" 2> "$OUT/cqt3r0c_s0_qtfw_s16.err" &
QTFW_SLACK_H=8 CRIT_UNDERFILL_W=300 "$PY" bench/tools/hold_ab.py cqt3r0c 0 off 15 crit > "$COUT/cqt3r0c_s0_v3u300.json" 2> "$COUT/cqt3r0c_s0_v3u300.err" &
wait
echo "qtfw_tune2 end $(date -Is)" >> "$OUT/driver.log"
