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

# -------------------------------------------------------------- data zone --
# Kafka and Postgres, via the dev override that publishes host listeners.
# Optional on purpose: the API runs without them (empty live panels, DEMO_LOTS
# keeps the scenario button working), so a missing Docker should degrade the
# stack rather than stop it.
info 'data zone'
DOCKER_OK=0
if command -v docker >/dev/null && docker info >/dev/null 2>&1; then
  DOCKER_OK=1
elif command -v docker >/dev/null; then
  c_warn 'docker present but not reachable (WSL integration? docker group?)'
else
  c_warn 'no docker'
fi

if (( DOCKER_OK )) && [[ "${SKIP_INFRA:-0}" != "1" ]]; then
  ( cd "$DISPATCH/infra" && docker compose \
      -f docker-compose.yml -f docker-compose.dev.yml \
      --profile data up -d kafka kafka-init postgres ) \
      >"$RUN/infra.log" 2>&1 \
    && c_ok 'kafka + postgres up' \
    || c_warn "compose failed, see $RUN/infra.log -- continuing without them"
  for _ in $(seq 1 30); do
    docker exec fab-data-postgres pg_isready -U fab -d fab >/dev/null 2>&1 \
      && { c_ok 'postgres ready on :25432'; break; }
    sleep 1
  done
  # A broker that is up but has no topics looks identical to a broken feed.
  docker exec fab-data-kafka /usr/bin/kafka-topics \
      --bootstrap-server localhost:9092 --list 2>/dev/null \
      | grep -q 'fab.lot.state' \
    && c_ok 'kafka topics present (incl. compacted state)' \
    || c_warn 'kafka topics missing -- cold-start bootstrap will find nothing'
else
  c_warn 'skipping data zone (SKIP_INFRA=1 or docker unavailable)'
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
  # Point the mirror at the broker only if one is actually up; otherwise leave
  # it unset so the API is honestly feedless rather than retrying a dead host.
  API_ENV=(DEMO_LOTS="${DEMO_LOTS:-1}")
  if (( DOCKER_OK )) && [[ "${SKIP_INFRA:-0}" != "1" ]]; then
    API_ENV+=(KAFKA_BROKERS="${KAFKA_BROKERS:-localhost:29092}"
              PGHOST="${PGHOST:-localhost}" PGPORT="${PGPORT:-25432}"
              PGDATABASE=fab PGUSER=fab PGPASSWORD=fab)
  fi
  ( cd "$DISPATCH" && setsid nohup env "${API_ENV[@]}" make api \
      >"$RUN/api.log" 2>&1 </dev/null & echo $! >"$RUN/api.pid" )
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
if (( DOCKER_OK )) && [[ "${SKIP_INFRA:-0}" != "1" ]]; then
  echo "    kafka       localhost:29092"
  echo "    postgres    postgresql://fab:fab@localhost:25432/fab"
fi
echo "    stop        scripts/dev-up.sh --stop"

info 'note'
cat <<'TXT'
    The dashboard is empty until something produces. The API consumer starts
    at `latest`, and cold start is solved by a snapshot rather than by
    replaying history:

      # once: simulate 90 days, publish a full WIP snapshot, then stream live
      baselines/pyscfabsim/.venv/bin/python3 bench/tools/sim_feed.py \
          --days 93 --warmup-days 90 --speed 10

      # afterwards: republish that snapshot from cache in ~2s
      baselines/pyscfabsim/.venv/bin/python3 bench/tools/sim_feed.py \
          --warmup-days 90 --snapshot-only

    Postgres is the run store for comparing runs, not live state -- that
    stays in Kafka's compacted topics.
TXT
echo
exit $rc
