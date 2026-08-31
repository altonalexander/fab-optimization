#!/usr/bin/env bash
# dev-up.sh -- bring up the local dev stack and prove it is actually serving.
#
# Local (no Docker): builds fab_scenario, starts the Flask API on :8000 and the
# Vite dev server on :5173, then health-checks both before claiming success.
# Live panels stay empty without Kafka -- see NOTE at the end of the run.
#
#   scripts/dev-up.sh          start, wait for health, print URLs
#   scripts/dev-up.sh --feed   also start the simulator feed (the dashboard is
#                              empty without a producer)
#   scripts/dev-up.sh --fresh  drop kafka + postgres volumes first, so no
#                              snapshot from an older run is bootstrapped
#   scripts/dev-up.sh --stop   stop whatever this script started
#   scripts/dev-up.sh --status show what is listening
#
# FEED_DAYS / FEED_WARMUP / FEED_SPEED override the feed defaults
# (180 days, 90-day warm-up, 20x realtime). The warm-up is simulated once and
# checkpointed to bench/snapshots/; later starts resume from it in seconds.
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

# lsof is blind on some WSL2 setups (returns nothing for a port our own user
# holds), which let a second api start, die on bind, and leave a stale one
# answering. ss is the fallback.
port_pid() {
  local pid
  pid="$(lsof -ti tcp:"$1" 2>/dev/null | head -1)"
  if [[ -z "$pid" ]] && command -v ss >/dev/null; then
    local line
    line="$(ss -ltn "sport = :$1" 2>/dev/null | grep -c LISTEN)"
    pid="$(ss -ltnp "sport = :$1" 2>/dev/null | grep -o 'pid=[0-9]*' | head -1 | cut -d= -f2)"
    # Listening but no visible pid (another user, a container): still held.
    [[ -z "$pid" && "$line" -gt 0 ]] && pid='?'
  fi
  printf '%s' "$pid"
}

stop_all() {
  info 'stopping'
  for name in api ui feed; do
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

FRESH=0
WANT_FEED=0
for arg in "$@"; do
  case "$arg" in
    --stop)   stop_all; exit 0 ;;
    --status) status;   exit 0 ;;
    --help|-h) sed -n '2,20p' "$0"; exit 0 ;;
    # Destructive and therefore explicit: drops the Kafka and Postgres volumes
    # so the next start has no snapshot from an older run to bootstrap from.
    # Stale state is what produced a day-5 fab drawn against a day-30 stream.
    --fresh)  FRESH=1 ;;
    --feed)   WANT_FEED=1 ;;
    *) echo "unknown option: $arg" >&2; exit 2 ;;
  esac
done

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

if (( DOCKER_OK )) && (( FRESH )); then
  c_warn 'dropping kafka + postgres volumes (--fresh)'
  ( cd "$DISPATCH/infra" && docker compose \
      -f docker-compose.yml -f docker-compose.dev.yml \
      --profile data down -v ) >"$RUN/fresh.log" 2>&1 \
    && c_ok 'volumes dropped' || c_warn 'teardown reported errors'
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
  # Retried: kafka-init runs as its own container and finishes a few seconds
  # after the broker reports healthy, so checking once races it and cries wolf.
  TOPICS_OK=0
  for _ in $(seq 1 20); do
    if docker exec fab-data-kafka /usr/bin/kafka-topics \
         --bootstrap-server localhost:9092 --list 2>/dev/null \
         | grep -q 'fab.lot.state'; then TOPICS_OK=1; break; fi
    sleep 1
  done
  (( TOPICS_OK )) \
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

# ---------------------------------------------------------------- feed -----
# The dashboard is empty without a producer, and the producer is a policy
# choice (dataset, days, speed, where to start), so it is opt-in rather than
# implied. One process does the snapshot AND the streaming: two processes mean
# two run ids and a timeline the mirror will correctly flag as stitched.
info 'feed'
SIM_PY="$REPO/baselines/pyscfabsim/.venv/bin/python3"
FEED_CMD=("$SIM_PY" "$REPO/bench/tools/sim_feed.py"
          --days "${FEED_DAYS:-180}" --warmup-days "${FEED_WARMUP:-90}"
          --speed "${FEED_SPEED:-20}")
if (( WANT_FEED )); then
  if [[ ! -x "$SIM_PY" ]]; then
    c_bad "no baseline venv at $SIM_PY -- see baselines/pyscfabsim/UPSTREAM.md"
  # Anchored to an interpreter, or any shell whose command line merely
  # mentions the script (a grep, a kill loop) reads as a running feed.
  elif [[ -n "$(pgrep -f '^[^ ]*python[0-9.]* [^ ]*bench/tools/sim_feed.py' || true)" ]]; then
    c_warn 'a feed is already running -- not starting a second'
  else
    # The feed writes its own pid: `$!` after setsid was the wrapper, one off
    # from the python process, so --stop and every kill since missed the feed
    # and left two producers on the same topics.
    ( cd "$REPO" && setsid nohup bash -c 'echo $$ >"$0"; exec "$@"' "$RUN/feed.pid" "${FEED_CMD[@]}" \
        >"$RUN/feed.log" 2>&1 </dev/null & )
    c_ok "feed starting (log $RUN/feed.log)"
  fi
else
  c_warn 'no feed started (--feed to start one). The dashboard will be empty:'
  printf '        %s \\\n            --days %s --warmup-days %s --speed %s\n' \
    "baselines/pyscfabsim/.venv/bin/python3 bench/tools/sim_feed.py" \
    "${FEED_DAYS:-180}" "${FEED_WARMUP:-90}" "${FEED_SPEED:-20}"
fi

info 'timeline'
# The one check that catches a stitched timeline: the mirror reports the run
# its snapshot came from and the run the live stream is on. Disagreement means
# every chart is drawing two fabs at once.
TL=$(curl -sf -m 5 "http://localhost:$API_PORT/api/state" 2>/dev/null \
     | "$API_PY" -c 'import json,sys
try:
    t = json.load(sys.stdin).get("timeline") or {}
except Exception:
    t = {}
print(t.get("consistent"), t.get("snapshot_day"), t.get("stream_day"))' 2>/dev/null)
case "$TL" in
  False*) c_bad  "timeline MISMATCH ($TL) -- snapshot and stream are different runs" ;;
  True*)  c_ok   "timeline consistent ($TL)" ;;
  *)      c_warn "timeline not established yet (no feed, or none seen)" ;;
esac

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
