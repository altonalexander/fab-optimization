#!/bin/bash
# crit v3 (priced under-min, qtfw s8 fallback) replication: scale 3 seed 2 (+ its
# qtfw s8 arm), scales 5 and 1 seed 0. 15 d from warmed qp050 checkpoints.
set -u
REPO=/home/alton/src/fab-optimization/.claude/worktrees/adr-0013-runs
PY=$REPO/baselines/pyscfabsim/.venv/bin/python3
OUT=$REPO/bench/results/crit_ab
QOUT=$REPO/bench/results/qtfw_ab
cd "$REPO" || exit 1
export QTF_LOOKAHEAD=6 QTFW_SLACK_H=8 CRIT_UNDERFILL_W=300
"$PY" bench/tools/hold_ab.py cqt3r0c 2 off 15 crit > "$OUT/cqt3r0c_s2_v3u300.json" 2> "$OUT/cqt3r0c_s2_v3u300.err" &
"$PY" bench/tools/hold_ab.py cqt3r0c 2 off 15 qtfw > "$QOUT/cqt3r0c_s2_qtfw_s8.json" 2> "$QOUT/cqt3r0c_s2_qtfw_s8.err" &
"$PY" bench/tools/hold_ab.py cqt5r0c 0 off 15 crit > "$OUT/cqt5r0c_s0_v3u300.json" 2> "$OUT/cqt5r0c_s0_v3u300.err" &
"$PY" bench/tools/hold_ab.py cqtr0c 0 off 15 crit > "$OUT/cqtr0c_s0_v3u300.json" 2> "$OUT/cqtr0c_s0_v3u300.err" &
wait
echo "crit3_ab end $(date -Is)" >> "$OUT/driver.log"
