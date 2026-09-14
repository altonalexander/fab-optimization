#!/usr/bin/env bash
# preview-ui.sh -- see dashboard changes against the RUNNING fab, without
# touching the stack that is serving it.
#
#   scripts/preview-ui.sh          local: preview API + Vite on loopback, hot reload
#   scripts/preview-ui.sh --public local, plus a GATED preview UI container and a
#                                  tunnel hostname, so others can see it
#   scripts/preview-ui.sh --stop   tear down whatever this script started
#   scripts/preview-ui.sh --status show what this script has running
#
# --public needs PREVIEW_HOST (default fab-dev.frontanalytics.com) and a
# Cloudflare token. On the homelab box:
#
#   sops exec-env ~/src/homelab/secrets/cloudflare.enc.env \
#     'scripts/preview-ui.sh --public'
#
# Why this exists
# ---------------
# `dispatch/infra/deploy.sh` rebuilds the real images and recreates the real
# containers. That is right for shipping and wrong for looking: recreating
# `api` drops the mirror it has spent hours accumulating from Kafka, and the
# stack on :8080 is the one behind the tunnel. So a UI change could only be
# reviewed by disturbing the thing it was meant to improve.
#
# This starts a SECOND, disposable API beside the real one -- same image build,
# same zone 2 + zone 3 networks -- and points a Vite dev server at it. Nothing
# the running stack owns is rebuilt, recreated or written to. Edit a .jsx, the
# browser hot-reloads; when the API changes, re-run this script.
#
# It must be an image whose consumer group is unique PER PROCESS, not per pid.
# This script was written believing pid was enough; it is not, because PID
# namespaces give two containers of the same image the same worker pid, so the
# preview joined the live mirror's group and Kafka split the topic-partitions
# between them. Both dashboards then looked healthy while each held half the
# fab. The preflight below refuses to start against an API image that still
# keys its group on the pid, because the damage is silent and lands on the
# stack this script exists to protect.
#
# What it deliberately does NOT share
# -----------------------------------
# IDLE_PAUSE_SECONDS is forced to 0. The API pauses the feed when no dashboard
# is connected, and the feed is shared: a preview API left running with nobody
# watching would otherwise stop the fab for the live dashboard too.
#
# It does mount the same sim-control file, so the header's pause/speed controls
# work -- and they drive the SHARED feed. Changing speed here changes it for
# everyone on :8080. That is the honest behaviour (there is one simulator), not
# a bug, but it is the one control in the preview that reaches outside it.
#
# Why --public is a container and not just the Vite port
# ------------------------------------------------------
# The entire access gate lives in the `ui` nginx image (infra/nginx.conf:
# auth_request in front of everything, codes and magic links behind it). Vite
# has none of it. Pointing a public hostname at the dev server would expose the
# dashboard, /api/*, /docs and the Vertex-backed assistant with no login at
# all. So --public builds the SAME ui image instead -- identical nginx.conf,
# identical gate -- and routes the hostname at that.
#
# Its nginx proxies to the service name `api`, which on the live enterprise
# network is the LIVE api. So the preview pair get a network of their own, and
# the preview API joins it aliased as `api`: inside fab-preview-net, `api` is
# unambiguously the preview one. The preview UI is on that network ONLY, so it
# can never resolve its way back to the live API. The tunnel is attached to the
# same network to reach it, which is the one change --public makes to a running
# container; `--stop` disconnects it again.
#
# KNOWN ISSUE (the same one dev-up.sh has): run this directly, not through a
# pipe. `preview-ui.sh | tee` blocks after the services start, because the Vite
# descendant keeps the write end of the pipe open even under setsid. The
# services come up correctly either way; only the caller hangs. Redirect to a
# file, or use --status to read the state back.
#
# Deliberately not `set -e`: report the component that failed and carry on.
set -uo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DISPATCH="$REPO/dispatch"
RUN="$REPO/.dev-run"              # pids and logs; gitignored
NAME=fab-preview-api
# NOT the tag compose builds (`fabdisp-api`). Building to that name would
# replace the image the live stack would be recreated from, which is exactly
# the kind of collateral damage this script exists to avoid.
IMAGE=fab-preview-api:local
API_PORT=${PREVIEW_API_PORT:-8001}
UI_PORT=${PREVIEW_UI_PORT:-5199}
DATA_NET=fab-zone2-data
ENT_NET=fab-zone3-enterprise

# --public only
UI_NAME=fab-preview-ui
UI_IMAGE=fab-preview-ui:local
PREVIEW_NET=fab-preview-net
TUNNEL=fab-enterprise-tunnel
PREVIEW_HOST=${PREVIEW_HOST:-fab-dev.frontanalytics.com}
GATED_PORT=${PREVIEW_GATED_PORT:-8081}   # loopback, for checking from the box
PUBLIC=0

mkdir -p "$RUN"

c_ok()   { printf '  \033[32mok\033[0m    %s\n' "$1"; }
c_warn() { printf '  \033[33mwarn\033[0m  %s\n' "$1"; }
c_bad()  { printf '  \033[31mfail\033[0m  %s\n' "$1"; }
info()   { printf '\n\033[1m%s\033[0m\n' "$1"; }

# Who is actually holding a port. Vite is started under setsid, so the `$!` of
# the launching subshell is the setsid wrapper -- which exits immediately,
# leaving the real node process in a new session with a pid nobody recorded.
# Recording that pid instead of the wrapper's is what makes --stop work.
# (lsof is blind on some WSL2 setups for a port our own user holds; ss is the
# fallback. Same reasoning as dev-up.sh.)
port_pid() {
  local pid
  pid="$(lsof -ti tcp:"$1" 2>/dev/null | head -1)"
  if [[ -z "$pid" ]] && command -v ss >/dev/null; then
    pid="$(ss -ltnp "sport = :$1" 2>/dev/null | grep -o 'pid=[0-9]*' | head -1 | cut -d= -f2)"
  fi
  printf '%s' "$pid"
}

vite_pid() { [[ -f "$RUN/preview-ui.pid" ]] && cat "$RUN/preview-ui.pid"; }

stop_all() {
  info 'stopping preview'
  # The recorded pid first, then whoever still holds the port -- vite spawns an
  # esbuild child, and killing the parent alone can leave the listener up.
  local p; p="$(vite_pid)"
  local stopped=0
  for t in "$p" "$(port_pid "$UI_PORT")"; do
    [[ -n "$t" && "$t" != '?' ]] || continue
    kill -0 "$t" 2>/dev/null || continue
    kill -- "-$t" 2>/dev/null || kill "$t" 2>/dev/null
    stopped=1
  done
  # Give the listener a moment to actually release before we report.
  for _ in 1 2 3 4 5; do [[ -z "$(port_pid "$UI_PORT")" ]] && break; sleep 1; done
  if [[ -n "$(port_pid "$UI_PORT")" ]]; then
    c_bad "port $UI_PORT is still held by pid $(port_pid "$UI_PORT")"
  elif [[ $stopped -eq 1 ]]; then
    c_ok 'vite stopped'
  else
    c_warn 'no vite from this script was running'
  fi
  rm -f "$RUN/preview-ui.pid"
  # The public hostname comes down FIRST, so there is never a window where it
  # resolves to a container that is going away.
  if docker ps -a --format '{{.Names}}' | grep -qx "$UI_NAME"; then
    if [[ -n "${CLOUDFLARE_API_TOKEN:-}" ]]; then
      ( cd "$DISPATCH/infra" && ./cf-tunnel.sh unroute "$PREVIEW_HOST" ) \
        && c_ok "$PREVIEW_HOST unrouted" \
        || c_warn "could not unroute $PREVIEW_HOST"
    else
      c_warn "no CLOUDFLARE_API_TOKEN -- $PREVIEW_HOST left routed"
      echo "        sops exec-env ~/src/homelab/secrets/cloudflare.enc.env \\"
      echo "          'cd dispatch/infra && ./cf-tunnel.sh unroute $PREVIEW_HOST'"
    fi
    docker rm -f "$UI_NAME" >/dev/null 2>&1 && c_ok "$UI_NAME removed"
  fi

  # Put the tunnel back the way we found it. Its own routes are untouched by
  # this; only the extra network attachment goes.
  if docker network disconnect "$PREVIEW_NET" "$TUNNEL" 2>/dev/null; then
    c_ok "$TUNNEL disconnected from $PREVIEW_NET"
  fi

  if docker rm -f "$NAME" >/dev/null 2>&1; then
    c_ok "$NAME removed"
  else
    c_warn "$NAME was not running"
  fi
  docker network rm "$PREVIEW_NET" >/dev/null 2>&1 && c_ok "$PREVIEW_NET removed"
  # The images are left behind on purpose: they are the expensive half, and the
  # next run rebuilds only the layers that actually changed.
  echo
  echo "The live stack was not touched. Check it with: docker ps"
}

status() {
  info 'preview status'
  if docker ps --format '{{.Names}}' | grep -qx "$NAME"; then
    c_ok "$NAME up -> http://127.0.0.1:$API_PORT"
  else
    c_warn "$NAME not running"
  fi
  local p; p="$(vite_pid)"
  if [[ -n "$p" ]] && kill -0 "$p" 2>/dev/null; then
    c_ok "vite pid $p -> http://127.0.0.1:$UI_PORT"
  else
    c_warn 'vite not running'
  fi
  if docker ps --format '{{.Names}}' | grep -qx "$UI_NAME"; then
    c_ok "$UI_NAME up -> https://$PREVIEW_HOST (gated)"
    local page; page=$(curl -s -o /dev/null -w '%{http_code}' "https://$PREVIEW_HOST/live" 2>/dev/null)
    [[ "$page" == 302 ]] && c_ok "  hostname answering, auth gate on (302)" \
                         || c_warn "  hostname returned $page (expected 302)"
  else
    c_warn "$UI_NAME not running (no public preview)"
  fi
  info 'the live stack (not managed by this script)'
  docker ps --filter name=fab- --format '  {{.Names}}\t{{.Status}}' \
    | grep -vE "^  ($NAME|$UI_NAME)\b" || c_warn 'no fab containers running'
}

case "${1:-}" in
  --stop)   stop_all; exit 0 ;;
  --status) status;   exit 0 ;;
  --public) PUBLIC=1 ;;
  '')       ;;
  -h|--help) sed -n '2,16p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
  *) echo "unknown option: $1 (try --help)" >&2; exit 1 ;;
esac

# ------------------------------------------------------------- preflight ----
info 'preflight'
command -v docker >/dev/null || { c_bad 'docker not found'; exit 1; }
docker info >/dev/null 2>&1   || { c_bad 'docker is not running'; exit 1; }
c_ok 'docker'

# The preview joins the live stack's networks, so the live stack has to be up.
# Without this the run fails later with a bare "network not found", which reads
# as a broken script rather than as "bring the fab up first".
missing=0
for n in "$DATA_NET" "$ENT_NET"; do
  if docker network inspect "$n" >/dev/null 2>&1; then
    c_ok "network $n"
  else
    c_bad "network $n not found"; missing=1
  fi
done
if [[ $missing -eq 1 ]]; then
  echo
  echo "  The fab stack does not appear to be up. Start it first:"
  echo "    cd dispatch/infra && docker compose --profile all up -d"
  echo "  Or, for a preview with no Docker at all, use scripts/dev-up.sh."
  exit 1
fi

# A port held by THIS script's previous run is fine -- re-running is the normal
# way to publish a change, and both halves are replaced below. A port held by
# anything else is someone else's, and we stop rather than fight over it.
for port in "$API_PORT" "$UI_PORT" ; do
  ss -ltn "sport = :$port" 2>/dev/null | grep -q LISTEN || continue
  mine=0
  if [[ "$port" == "$API_PORT" ]]; then
    docker ps --format '{{.Names}}' | grep -qx "$NAME" && mine=1
  else
    # Ours if it is the pid we recorded, or if the log we wrote is its cwd's
    # -- the pid file is the reliable half, so that is what is checked.
    [[ "$(port_pid "$port")" == "$(vite_pid)" && -n "$(vite_pid)" ]] && mine=1
  fi
  if [[ $mine -eq 1 ]]; then
    c_warn "port $port held by this script's previous run (will be replaced)"
  else
    c_bad "port $port is in use by something else -- set PREVIEW_API_PORT / PREVIEW_UI_PORT"
    exit 1
  fi
done

# Replace our own Vite before starting a new one, so the port is free by the
# time we bind it.
if [[ -n "$(vite_pid)" ]] && [[ "$(port_pid "$UI_PORT")" == "$(vite_pid)" ]]; then
  kill -- "-$(vite_pid)" 2>/dev/null || kill "$(vite_pid)" 2>/dev/null
  for _ in 1 2 3 4 5; do [[ -z "$(port_pid "$UI_PORT")" ]] && break; sleep 1; done
  rm -f "$RUN/preview-ui.pid"
  c_ok 'previous vite stopped'
fi

# ----------------------------------------------------------------- build ----
# A preview whose mirror shares a consumer group with the live one takes half
# the live dashboard's feed and neither side reports anything wrong. Checked
# against the source rather than the built image, since that is what is about
# to be built.
if grep -q 'group.id.*os\.getpid()' "$DISPATCH/api/main.py"; then
  c_bad 'api/main.py still keys its Kafka group.id on os.getpid()'
  echo "        Two containers of one image share a pid namespace position, so"
  echo "        this preview would join the LIVE mirror's group and Kafka would"
  echo "        split the partitions between them -- silently, on both sides."
  echo "        Make the group id unique per process before previewing."
  exit 1
fi
c_ok 'api consumer groups are unique per process'

info "building $IMAGE"
if docker build -f "$DISPATCH/infra/Dockerfile.api" -t "$IMAGE" "$REPO" \
     >"$RUN/preview-build.log" 2>&1; then
  c_ok "image built (log: .dev-run/preview-build.log)"
else
  c_bad "build failed -- see $RUN/preview-build.log"
  tail -20 "$RUN/preview-build.log"
  exit 1
fi

# ------------------------------------------------------------------- api ----
info "starting $NAME on :$API_PORT"

# Postgres holds the access codes AND the run store, and the preview shares the
# real one. The password in docker-compose.yml is a placeholder the actual
# deployment overrides, so hardcoding it here gives a preview that cannot log
# anyone in and shows an empty Results page -- while still looking healthy,
# because the auth gate fails closed and reads as "working". Read the value off
# the running api instead; it is the only thing on this box that knows it.
# Captured into a variable and passed straight to docker run: never echoed.
LIVE_API=${LIVE_API_NAME:-fab-data-api}
PGPW="$(docker inspect "$LIVE_API" --format '{{range .Config.Env}}{{println .}}{{end}}' 2>/dev/null \
        | sed -n 's/^PGPASSWORD=//p' | head -1)"
if [[ -n "$PGPW" ]]; then
  c_ok "postgres password inherited from $LIVE_API"
else
  PGPW=${PGPASSWORD:-fab}
  c_warn "could not read PGPASSWORD from $LIVE_API -- using the compose default"
  echo "        If login fails or Results is empty, that is why."
fi

# Two more things the live api has that a bare `docker run` does not, both read
# off the running container for the same reason as the password -- it is the
# only thing on this box that knows the answer.
#
#   sim-control  the playback pause/speed handoff to the feed. Without it the
#                header's speed control reports itself unavailable and the
#                paused-fab modal never fires, so a paused fab looks simply
#                empty and reads as a broken preview.
#   gcp sa.json  the assistant's Vertex credentials. Without it the rail says
#                "Assistant unavailable", which is honest and harmless.
SIM_VOL="$(docker inspect "$LIVE_API" \
  --format '{{range .Mounts}}{{if eq .Destination "/var/run/fab-sim"}}{{.Name}}{{end}}{{end}}' 2>/dev/null)"
SIM_MOUNT=()
if [[ -n "$SIM_VOL" ]]; then
  SIM_MOUNT=(-v "$SIM_VOL:/var/run/fab-sim")
  c_ok "sim-control volume inherited ($SIM_VOL)"
else
  c_warn 'no sim-control volume found -- playback speed/pause will be inert'
fi

GCP_SRC="$(docker inspect "$LIVE_API" \
  --format '{{range .Mounts}}{{if eq .Destination "/var/secrets/gcp/sa.json"}}{{.Source}}{{end}}{{end}}' 2>/dev/null)"
GCP_MOUNT=()
if [[ -n "$GCP_SRC" && -f "$GCP_SRC" ]]; then
  GCP_MOUNT=(-v "$GCP_SRC:/var/secrets/gcp/sa.json:ro"
             -e "GOOGLE_APPLICATION_CREDENTIALS=/var/secrets/gcp/sa.json")
  c_ok 'assistant credentials inherited'
else
  c_warn 'no assistant credentials -- the rail will say "Assistant unavailable"'
fi

docker rm -f "$NAME" >/dev/null 2>&1
# Mirrors the `api` service in docker-compose.yml. Kept explicit rather than
# driven by compose: `compose run` on that service would attach to the live
# project and can recreate its dependencies, which is the one thing this
# script promises not to do.
if docker run -d --name "$NAME" \
     --network "$DATA_NET" \
     -e KAFKA_BROKERS=kafka:9092 \
     -e READ_ONLY=true \
     -e FAB_ZONE=boundary-2-3 \
     -e SOLVER=cpsat \
     -e PGHOST=postgres -e PGPORT=5432 \
     -e PGDATABASE=fab -e PGUSER=fab -e PGPASSWORD="$PGPW" \
     -e ZONES_FILE=/etc/fab-zones/zones.yaml \
     -e SIM_CONTROL_FILE=/var/run/fab-sim/sim_control.json \
     -e IDLE_PAUSE_SECONDS=0 \
     -e "GOOGLE_CLOUD_PROJECT=${GOOGLE_CLOUD_PROJECT:-}" \
     -v "$DISPATCH/config":/etc/fab:ro \
     -v "$DISPATCH/infra/zones.yaml":/etc/fab-zones/zones.yaml:ro \
     "${SIM_MOUNT[@]}" "${GCP_MOUNT[@]}" \
     -p "127.0.0.1:$API_PORT:8000" \
     "$IMAGE" >/dev/null; then
  docker network connect "$ENT_NET" "$NAME" 2>/dev/null
  c_ok "$NAME started"
else
  c_bad "could not start $NAME"
  exit 1
fi

# The mirror bootstraps from the compacted state topics, which takes a few
# seconds on a fab this size. Waiting for real content, not just a 200, so the
# browser does not open on an empty dashboard and read as broken.
printf '  '
ready=0
for _ in $(seq 1 60); do
  total="$(curl -fsS "http://127.0.0.1:$API_PORT/api/tools" 2>/dev/null \
           | sed -n 's/.*"total":\([0-9]*\).*/\1/p')"
  if [[ -n "${total:-}" && "$total" -gt 0 ]]; then ready=1; break; fi
  printf '.'; sleep 1
done
printf '\n'
if [[ $ready -eq 1 ]]; then
  c_ok "api answering with $total tools mirrored"
else
  c_warn 'api is up but has mirrored no tools yet -- is the feed running?'
  echo "        docker logs $NAME | tail"
fi

# -------------------------------------------------------------------- ui ----
info "starting vite on :$UI_PORT"
if [[ ! -d "$DISPATCH/ui/node_modules" ]]; then
  c_warn 'installing ui dependencies (first run)'
  ( cd "$DISPATCH/ui" && npm install --no-audit --no-fund ) \
    >"$RUN/preview-npm.log" 2>&1 \
    || { c_bad "npm install failed -- see $RUN/preview-npm.log"; exit 1; }
fi
# setsid so it survives this shell, and its own process group so --stop can
# take the esbuild child down with it.
( cd "$DISPATCH/ui" && \
  VITE_API_TARGET="http://127.0.0.1:$API_PORT" \
  setsid npx vite --port "$UI_PORT" --host 127.0.0.1 \
    >"$RUN/preview-ui.log" 2>&1 </dev/null & )

for _ in $(seq 1 30); do
  curl -fsS -o /dev/null "http://127.0.0.1:$UI_PORT/live" 2>/dev/null && break
  sleep 1
done
if curl -fsS -o /dev/null "http://127.0.0.1:$UI_PORT/live" 2>/dev/null; then
  # Recorded only now that something is listening, and read off the port
  # rather than off `$!` -- see port_pid.
  port_pid "$UI_PORT" >"$RUN/preview-ui.pid"
  c_ok "vite serving (pid $(vite_pid))"
else
  c_bad "vite did not come up -- see $RUN/preview-ui.log"
  exit 1
fi

# ---------------------------------------------------------------- public ----
if [[ $PUBLIC -eq 1 ]]; then
  info "gated preview for $PREVIEW_HOST"

  if [[ -z "${CLOUDFLARE_API_TOKEN:-}" ]]; then
    c_bad 'CLOUDFLARE_API_TOKEN is not set'
    echo "        sops exec-env ~/src/homelab/secrets/cloudflare.enc.env \\"
    echo "          'scripts/preview-ui.sh --public'"
    exit 1
  fi
  c_ok 'cloudflare token present'

  docker network create "$PREVIEW_NET" >/dev/null 2>&1
  c_ok "network $PREVIEW_NET"

  # Aliased `api` so the preview nginx's `proxy_pass http://api:8000` lands on
  # the preview API. Idempotent: already-connected is not an error worth
  # stopping for, it is the second run.
  docker network connect --alias api "$PREVIEW_NET" "$NAME" 2>/dev/null \
    && c_ok "$NAME joined $PREVIEW_NET as 'api'" \
    || c_ok "$NAME already on $PREVIEW_NET"

  info "building $UI_IMAGE"
  # Context is dispatch/, matching compose's `context: ..` from infra/.
  if docker build -f "$DISPATCH/infra/Dockerfile.ui" -t "$UI_IMAGE" "$DISPATCH" \
       >"$RUN/preview-ui-build.log" 2>&1; then
    c_ok 'image built (log: .dev-run/preview-ui-build.log)'
  else
    c_bad "build failed -- see $RUN/preview-ui-build.log"
    tail -20 "$RUN/preview-ui-build.log"
    exit 1
  fi

  docker rm -f "$UI_NAME" >/dev/null 2>&1
  # On PREVIEW_NET only: attaching it to the enterprise network too would make
  # `api` ambiguous between the preview and the live one, and Docker's DNS
  # would be free to pick either.
  if docker run -d --name "$UI_NAME" --network "$PREVIEW_NET" \
       -p "127.0.0.1:$GATED_PORT:80" "$UI_IMAGE" >/dev/null; then
    c_ok "$UI_NAME started"
  else
    c_bad "could not start $UI_NAME"; exit 1
  fi

  docker network connect "$PREVIEW_NET" "$TUNNEL" 2>/dev/null \
    && c_ok "$TUNNEL joined $PREVIEW_NET" \
    || c_ok "$TUNNEL already on $PREVIEW_NET"

  # Prove the gate before publishing the hostname. A preview that 200s here
  # would be an unauthenticated dashboard about to be put on the internet.
  sleep 3
  page=$(curl -s -o /dev/null -w '%{http_code}' "http://127.0.0.1:$GATED_PORT/live")
  apic=$(curl -s -o /dev/null -w '%{http_code}' "http://127.0.0.1:$GATED_PORT/api/tools")
  if [[ "$page" == 302 && "$apic" == 401 ]]; then
    c_ok "auth gate verified (pages 302 -> /login, api 401)"
  else
    c_bad "auth gate NOT working (/live=$page /api/tools=$apic) -- refusing to route"
    echo "        The hostname was not published. Investigate before retrying."
    exit 1
  fi

  info "routing $PREVIEW_HOST"
  if ( cd "$DISPATCH/infra" && ./cf-tunnel.sh route "$PREVIEW_HOST" "http://$UI_NAME:80" ); then
    c_ok 'route added (existing routes are preserved)'
  else
    c_bad 'routing failed'; exit 1
  fi
fi

info 'preview is up'
cat <<EOF
  dashboard   http://127.0.0.1:$UI_PORT
  api         http://127.0.0.1:$API_PORT     (docs at /docs)

  Hot reload is on: edit dispatch/ui/src/*.jsx and the page updates. A change
  to dispatch/api/ needs this script run again (it rebuilds the image).

  The live stack on :8080 is untouched and still serving. The one control here
  that reaches it is the header's playback speed/pause -- there is a single
  simulator behind both.
EOF
if [[ $PUBLIC -eq 1 ]]; then
cat <<EOF

  shareable   https://$PREVIEW_HOST      (same login as production)
  gated, local  http://127.0.0.1:$GATED_PORT

  That hostname serves a BUILD, not the dev server, so it does not hot-reload.
  Re-run --public to publish new changes to it.
EOF
fi
cat <<EOF

  scripts/preview-ui.sh --stop     tear this down
  scripts/preview-ui.sh --status   what is running
EOF
