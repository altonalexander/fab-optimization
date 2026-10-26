#!/bin/bash
# The testbed's OWN queue-time semantics: scrap on the first violation.
#
# WSC 2020 §2.1 says a lot that violates its CQT has to be scrapped. Every run
# this project has ever published instead reworks it to the entrance step up to
# three times (ADR 0016 §6). That was a deliberate, declared choice, but it was
# never TESTED against the paper's own rule -- and it is the more expensive of
# the two for a fab at 80% utilisation, because rework adds load where scrap
# removes it.
#
# --cqt-max-rework 0 now means scrap-on-first (audit F4; before 2026-09-16 it
# meant unbounded and the paper's semantics were inexpressible). Scale 1 is the
# dataset's own windows -- the configuration where rework-then-scrap collapsed
# every seed. Scale 8 is the control.
set -u
REPO=/home/alton/src/fab-optimization/.claude/worktrees/adr-0013-runs
PY=$REPO/baselines/pyscfabsim/.venv/bin/python3
OUT=$REPO/bench/results/cqtdiag
mkdir -p "$OUT"

run() {
  local seed=$1 scale=$2
  local tag="scrapfirst_s${seed}_x${scale}"
  [ -s "$OUT/${tag}.json" ] && { echo "SKIP $tag"; return 0; }
  cd "$REPO" || return 1
  QT_PROMOTE_FRAC=0.50 "$PY" bench/tools/compare.py \
      --days 180 --warmup-days 90 --warmup-dispatcher qt \
      --seed "$seed" --rules qt \
      --cqt --cqt-scale "$scale" --cqt-max-rework 0 \
      --out "$OUT/${tag}.json" > "$OUT/${tag}.log" 2>&1
  echo "DONE $tag rc=$? $(date -Is)"
}
export -f run
export REPO PY OUT

echo "scrapfirst start $(date -Is)"
for seed in 0 1 2 3 4; do for scale in 1 8; do echo "$seed $scale"; done; done \
  | xargs -P 10 -n 2 bash -c 'run "$0" "$1"'
echo "scrapfirst end $(date -Is)"
