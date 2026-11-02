#!/bin/bash
# Is the scale-1 collapse caused by the WINDOWS, or by our REWORK policy?
#
# WSC 2020 §2.1 scraps a lot that violates its queue time. We rework it to the
# entrance step up to three times and scrap only on the fourth (ADR 0016 §6,
# audit finding F4). Rework ADDS load to a fab already at 80% utilisation;
# scrap REMOVES it. So a high violation rate could be a positive feedback loop
# that is OURS, not the dataset's: violations -> rework -> congestion -> more
# violations.
#
# The discriminating run is detection-only (--cqt-no-rework): windows are
# observed and counted but carry no consequence, so no load is added or
# removed. The qt rule still STEERS on the windows, so this is not simply the
# no-cqt fab. If the fab is viable at scale 1 under detection-only -- the scale
# where every rule collapsed -- then the windows are survivable and our rework
# policy is what kills it.
#
# Scale 8 runs alongside as the control (known viable with enforcement on).
set -u
REPO=/home/alton/src/fab-optimization/.claude/worktrees/adr-0013-runs
PY=$REPO/baselines/pyscfabsim/.venv/bin/python3
OUT=$REPO/bench/results/cqtdiag
mkdir -p "$OUT"

run() {
  local seed=$1 scale=$2
  local tag="detect_s${seed}_x${scale}"
  [ -s "$OUT/${tag}.json" ] && { echo "SKIP $tag"; return 0; }
  cd "$REPO" || return 1
  QT_PROMOTE_FRAC=0.50 "$PY" bench/tools/compare.py \
      --days 180 --warmup-days 90 --warmup-dispatcher qt \
      --seed "$seed" --rules qt \
      --cqt --cqt-scale "$scale" --cqt-no-rework \
      --out "$OUT/${tag}.json" > "$OUT/${tag}.log" 2>&1
  echo "DONE $tag rc=$? $(date -Is)"
}
export -f run
export REPO PY OUT

echo "diag start $(date -Is)"
for seed in 0 1 2 3 4; do for scale in 1 8; do echo "$seed $scale"; done; done \
  | xargs -P 10 -n 2 bash -c 'run "$0" "$1"'
echo "diag end $(date -Is)"
