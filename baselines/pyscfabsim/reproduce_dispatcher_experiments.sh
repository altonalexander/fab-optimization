#!/usr/bin/env bash
# Reproduce the FIFO / CR dispatcher experiments.
# Override the sweep with env vars, e.g.:
#   PYSCFABSIM_DAYS=30 PYSCFABSIM_SEEDS=0,1 ./reproduce_dispatcher_experiments.sh
set -euo pipefail
cd "$(dirname "$0")"

PY=.venv/bin/python
[ -x "$PY" ] || { echo "Missing $PY - create it first (see README)." >&2; exit 1; }

export WANDB_MODE=${WANDB_MODE:-offline}
export WANDB_SILENT=${WANDB_SILENT:-true}

mkdir -p greedy
"$PY" greedy_runner.py
"$PY" eval_results.py > greedy/_greedy_sum.txt
"$PY" build_dashboard.py --days "${PYSCFABSIM_DAYS:-730}"
"$PY" build_dashboard.py --days "${PYSCFABSIM_DAYS:-730}" --standalone --out dashboard_local.html
echo "Summary written to greedy/_greedy_sum.txt"
echo "Dashboard: open dashboard_local.html in a browser"
