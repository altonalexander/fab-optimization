#!/usr/bin/env bash
# dev-up.sh -- bring up the local dev stack and prove it is actually serving.
#
# Local (no Docker): builds fab_scenario, starts the Flask API on :8000 and the
# Vite dev server on :5173, then health-checks both before claiming success.
# Live panels stay empty without Kafka -- see NOTE at the end of the run.
#
#   scripts/dev-up.sh          start, wait for health, print URLs
#   scripts/dev-up.sh --stop   stop whatever this script started
#   scripts/dev-up.sh --status show what is listening
#
# KNOWN ISSUE: run this directly, not through a pipe. Piping it
# (`dev-up.sh | tee`) blocks after the services start -- a descendant keeps the
# write end of the pipe open even with setsid and </dev/null. The services come
# up correctly either way; only the caller hangs. Use --status to read state.
#
# Deliberately not `set -e`: we want to report a failed component and keep
# going rather than abort with a bare exit code.
set -uo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DISPATCH="$REPO/dispatch"
RUN="$REPO/.dev-run"          # pids and logs; gitignored
API_PORT=8000
UI_PORT=5173

mkdir -p "$RUN"

c_ok()   { printf '  \033[32mok\033[0m    %s\n' "$1"; }
c_warn() { printf '  \033[33mwarn\033[0m  %s\n' "$1"; }
c_bad()  { printf '  \033[31mfail\033[0m  %s\n' "$1"; }
info()   { printf '\n\033[1m%s\033[0m\n' "$1"; }

port_pid() { lsof -ti tcp:"$1" 2>/dev/null | head -1; }

stop_all() {
  info 'stopping'
  for name in api ui; do
    pidfile="$RUN/$name.pid"
    if [[ -f "$pidfile" ]]; then
      pid="$(cat "$pidfile")"
      # Kill the process group: make spawns python, npm spawns vite.
      if kill -0 "$pid" 2>/dev/null; then
        # setsid made this the process-group leader, so a negative pid takes
        # the whole tree (make -> python, npm -> vite) in one signal.
        kill -TERM -- "-$pid" 2>/dev/null || { pkill -P "$pid" 2>/dev/null; kill "$pid" 2>/dev/null; }
        c_ok "$name stopped (pid $pid)"
      else
        c_warn "$name not running"
      fi
      rm -f "$pidfile"
    else
      c_warn "$name has no pidfile"
    fi
  done
  # Anything still holding the ports was not started by us; say so, do not kill.
  for p in "$API_PORT" "$UI_PORT"; do
    pid="$(port_pid "$p")"
    [[ -n "$pid" ]] && c_warn "port $p still held by pid $pid (not started by this script)"
  done
  echo
}

status() {
  info 'status'
  for pair in "api:$API_PORT" "ui:$UI_PORT"; do
    name="${pair%%:*}"; p="${pair##*:}"
    pid="$(port_pid "$p")"
    if [[ -n "$pid" ]]; then c_ok "$name listening on :$p (pid $pid)"
    else c_warn "$name not listening on :$p"; fi
  done
  echo
}

case "${1:-}" in
  --stop)   stop_all; exit 0 ;;
  --status) status;   exit 0 ;;
  --help|-h) sed -n '2,12p' "$0"; exit 0 ;;
esac

# ---------------------------------------------------------------- prereqs ---
info 'prerequisites'
missing=0
for t in g++ make node npm curl; do
  if command -v "$t" >/dev/null; then c_ok "$t"
  else c_bad "$t not found"; missing=1; fi
done
command -v lsof >/dev/null || c_warn 'lsof not found -- port checks will be blind'

# The API venv has no pip (uv-created), so uv is how we install into it.
UV="$(command -v uv || true)"
[[ -n "$UV" ]] && c_ok 'uv' || c_warn 'uv not found -- cannot auto-install API deps'

if (( missing )); then
  c_bad 'missing prerequisites, stopping'
  exit 1
fi

# ------------------------------------------------------------- api deps ----
info 'api dependencies'
API_VENV="$DISPATCH/api/.venv"
API_PY="$API_VENV/bin/python3"
if [[ ! -x "$API_PY" ]]; then
  if [[ -z "$UV" ]]; then
    c_bad "no $API_VENV and no uv to build it -- see dispatch/api/requirements.txt"
    exit 1
  fi
  c_warn 'creating api/.venv'
  # python3 -m venv fails here: Ubuntu ships python3.12 without ensurepip.
  "$UV" venv "$API_VENV" >/dev/null 2>&1 || { c_bad 'uv venv failed'; exit 1; }
fi
if ! "$API_PY" -c 'import flask, confluent_kafka, yaml' 2>/dev/null; then
  c_warn 'installing api requirements'
  ( cd "$DISPATCH/api" && VIRTUAL_ENV="$API_VENV" "$UV" pip install -r requirements.txt ) \
    >"$RUN/api-install.log" 2>&1 \
    || { c_bad "install failed, see $RUN/api-install.log"; exit 1; }
fi
"$API_PY" -c 'import flask, confluent_kafka, yaml' 2>/dev/null \
  && c_ok 'flask, confluent-kafka, pyyaml' \
  || { c_bad 'api imports still failing'; exit 1; }

# -------------------------------------------------------------- ui deps ----
info 'ui dependencies'
if [[ ! -d "$DISPATCH/ui/node_modules" ]]; then
  c_warn 'running npm install'
  ( cd "$DISPATCH/ui" && npm install ) >"$RUN/ui-install.log" 2>&1 \
    || { c_bad "npm install failed, see $RUN/ui-install.log"; exit 1; }
fi
c_ok 'node_modules present'

# ---------------------------------------------------------------- build ----
info 'build'
if ( cd "$DISPATCH" && make scenario ) >"$RUN/build.log" 2>&1; then
  c_ok 'fab_scenario'
else
  c_bad "build failed, see $RUN/build.log"
  exit 1
fi

# ---------------------------------------------------------------- start ----
info 'starting services'
if [[ -n "$(port_pid "$API_PORT")" ]]; then
  c_warn "port $API_PORT already in use -- not starting a second api"
else
  # setsid + </dev/null detaches fully. Without it the child inherits this
  # script's stdout, and a caller piping us (dev-up.sh | tail) blocks forever
  # waiting for a pipe the server is still holding open.
  ( cd "$DISPATCH" && setsid nohup make api >"$RUN/api.log" 2>&1 </dev/null & echo $! >"$RUN/api.pid" )
  c_ok "api starting (log $RUN/api.log)"
fi
if [[ -n "$(port_pid "$UI_PORT")" ]]; then
  c_warn "port $UI_PORT already in use -- not starting a second vite"
else
  ( cd "$DISPATCH/ui" && setsid nohup npm run dev >"$RUN/ui.log" 2>&1 </dev/null & echo $! >"$RUN/ui.pid" )
  c_ok "ui starting (log $RUN/ui.log)"
fi

# ---------------------------------------------------------------- health ---
info 'health'
wait_for() {  # url, label, tries
  for _ in $(seq 1 "$3"); do
    curl -sf -m 2 "$1" >/dev/null 2>&1 && { c_ok "$2"; return 0; }
    sleep 1
  done
  c_bad "$2 did not come up"
  return 1
}
rc=0
wait_for "http://localhost:$API_PORT/health"          'api /health'        30 || rc=1
wait_for "http://localhost:$UI_PORT/"                 'ui index'           30 || rc=1
wait_for "http://localhost:$UI_PORT/api/state"        'ui -> api proxy'    15 || rc=1

info 'urls'
echo "    dashboard   http://localhost:$UI_PORT/"
echo "    api         http://localhost:$API_PORT/health"
echo "    stop        scripts/dev-up.sh --stop"

info 'note'
cat <<'TXT'
    The API reads live state from Kafka (KAFKA_BROKERS, default kafka:9092),
    which this local mode does not start. /api/state stays zeroed and
    /api/stream stays silent until you run `make infra-up` in dispatch/.
    Everything above still verifies that the stack is wired and serving.
TXT
echo
exit $rc
