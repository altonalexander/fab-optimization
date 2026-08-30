#!/usr/bin/env bash
# smoke.sh -- start the API against a fixture feed and assert it serves sane
# numbers. Closes the biggest coverage gap in the repo: 56 C++ tests, and until
# now nothing at all for the API, the producer or the floorplan.
#
# Every assertion here corresponds to a bug that actually shipped:
#   tools > 0            the feed loop read nothing and reported nothing
#   delays not placed    "Delay" starts with "DE", so 400 placeholders landed
#                        in the etch bays
#   unplaced == 0        a family with no zone mapping vanishes from the map
#   per-tool decisions   the shared 500-deep ring evicted a tool's history
#                        before anyone could drill into it
#   compare assigns      /api/scenario/compare 500'd on every dashboard click
#
#   scripts/smoke.sh            build a fixture, run, assert
#   scripts/smoke.sh --keep     leave the API running afterwards
set -uo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PORT="${SMOKE_PORT:-8111}"          # not 8000: never fight a running dev API
RUN="$(mktemp -d)"
FEED="$RUN/feed.jsonl"
# Both venvs are gitignored, so a fresh clone or a git worktree will not have
# them. Overridable rather than fatal: scripts/dev-up.sh builds the API one.
API_PY="${SMOKE_API_PY:-$REPO/dispatch/api/.venv/bin/python3}"
SIM_PY="${SMOKE_SIM_PY:-$REPO/baselines/pyscfabsim/.venv/bin/python3}"
KEEP=0
[[ "${1:-}" == "--keep" ]] && KEEP=1

pass=0; fail=0
ok()   { printf '  \033[32mpass\033[0m  %s\n' "$1"; pass=$((pass+1)); }
bad()  { printf '  \033[31mFAIL\033[0m  %s\n' "$1"; fail=$((fail+1)); }
note() { printf '\n\033[1m%s\033[0m\n' "$1"; }

cleanup() {
  if (( KEEP )); then
    echo "  API left running on :$PORT (pid ${API_PID:-?}), fixture in $RUN"
    return
  fi
  [[ -n "${API_PID:-}" ]] && kill -TERM -- "-$API_PID" 2>/dev/null
  rm -rf "$RUN"
}
trap cleanup EXIT

# assert_num <label> <actual> <op> <expected>
assert_num() {
  local label="$1" got="$2" op="$3" want="$4"
  if [[ -z "$got" || "$got" == "null" ]]; then bad "$label (got nothing)"; return; fi
  if awk "BEGIN{exit !($got $op $want)}"; then ok "$label ($got $op $want)"
  else bad "$label: got $got, expected $op $want"; fi
}

jqp() { "$API_PY" -c "import json,sys;d=json.load(sys.stdin);print($1)" 2>/dev/null; }

# Pull one labelled line out of a test script's own summary, so a passing
# check still reports the number it passed on rather than just "ok".
jqp_grep() { awk -v k="$1" '$1==k{$1="";sub(/^ +/,"");print;exit}' "$2"; }

note 'prerequisites'
for p in "$API_PY" "$SIM_PY"; do
  [[ -x "$p" ]] && ok "$(basename "$(dirname "$(dirname "$p")")") venv" \
                || { bad "missing interpreter $p"; exit 1; }
done

note 'fixture'
# Short and unpaced: this is a correctness check, not a benchmark.
if "$SIM_PY" "$REPO/bench/tools/sim_feed.py" --out "$FEED" --days 1 --speed 0 \
     --truncate >/dev/null 2>&1; then
  ok "generated $(wc -l < "$FEED") events"
else
  bad 'sim_feed failed'; exit 1
fi

note 'tool recovery'
# Unit-level first: the endpoint checks below only see the aggregate, and an
# aggregate that looks healthy can still be hiding a broken recovery path.
if "$API_PY" "$REPO/dispatch/api/t_recovery.py" >"$RUN/recovery.log" 2>&1; then
  ok "recovery unit tests ($(grep -c '  ok ' "$RUN/recovery.log") checks)"
else
  bad 'recovery unit tests'; sed -n '/FAIL/p' "$RUN/recovery.log"
fi
# The feed must emit a matching online=1 for its outages.
if "$API_PY" "$REPO/dispatch/api/t_feed_recovery.py" "$FEED" >"$RUN/feed.log" 2>&1; then
  ok "feed pairs outages with recoveries ($(jqp_grep 'recoveries' "$RUN/feed.log"))"
else
  bad 'feed is one-way'; sed -n '/FAIL/p' "$RUN/feed.log"
fi
# ...and the mirror must hold the roster up even when it does not.
if "$API_PY" "$REPO/dispatch/api/t_watchdog_replay.py" "$FEED" >"$RUN/wd.log" 2>&1; then
  ok "watchdog holds the roster on a one-way feed ($(jqp_grep 'online' "$RUN/wd.log"))"
else
  bad 'watchdog did not hold the roster'; sed -n '/FAIL/p' "$RUN/wd.log"
fi

note 'api'
cd "$REPO/dispatch"
setsid env PORT="$PORT" DEMO_LOTS=1 FEED_FILE="$FEED" \
  SCENARIO_BIN="$REPO/dispatch/fab_scenario" \
  TOOL_CONFIG="$REPO/dispatch/config/fab_tools.json" \
  ZONES_FILE="$REPO/dispatch/infra/zones.yaml" \
  "$API_PY" api/main.py >"$RUN/api.log" 2>&1 </dev/null &
API_PID=$!
for _ in $(seq 1 40); do
  curl -sf -m 2 "http://localhost:$PORT/health" >/dev/null 2>&1 && break
  sleep 1
done
B="http://localhost:$PORT"
curl -sf -m 3 "$B/health" >/dev/null && ok 'health' || { bad 'api never came up'; exit 1; }

# The mirror replays the fixture from offset 0; give it a moment to catch up.
sleep 6

note '/api/tools'
T=$(curl -sf -m 10 "$B/api/tools")
assert_num 'tools discovered'  "$(echo "$T" | jqp "d['total']")"        '>' 500
assert_num 'groups discovered' "$(echo "$T" | jqp "len(d['groups'])")"  '>' 10

note '/api/tools/availability'
# The roster must not decay. Tools go down via TOOL_STATUS and used to have no
# way back: the feed emitted online=0 and never online=1, so a long enough run
# drained the fab to zero online tools. 95% is well under a real fab's
# availability and well over anything a one-way feed produces.
sleep 3                                   # let the sampler take a point or two
A=$(curl -sf -m 10 "$B/api/tools/availability")
assert_num 'roster online'  "$(echo "$A" | jqp "100*d['now']['online']/max(1,d['now']['total'])")" '>' 95
assert_num 'series sampled' "$(echo "$A" | jqp "len(d['ts'])")"     '>' 0
assert_num 'series aligned' "$(echo "$A" | jqp "len(d['ts'])-len(d['online'])")" '==' 0
assert_num 'online <= total' "$(echo "$A" | jqp "max([o-t for o,t in zip(d['online'],d['total'])] or [0])")" '<=' 0

note '/api/layout'
L=$(curl -sf -m 15 "$B/api/layout")
assert_num 'cells'            "$(echo "$L" | jqp "len(d['cells'])")"    '==' 96
assert_num 'equipment placed' "$(echo "$L" | jqp "d['placed']")"        '>'  900
assert_num 'unplaced tools'   "$(echo "$L" | jqp "len(d['unplaced'])")" '==' 0
assert_num 'delay groups held out' \
           "$(echo "$L" | jqp "len(d['delays'])")"                      '>'  0
# The one that regresses silently: Delay_* must never receive a cell.
DP=$(echo "$L" | jqp "sum(1 for t in d['assign'] if t.lower().startswith('delay'))")
assert_num 'delays placed on floor' "$DP" '==' 0

note '/api/layout/state'
S=$(curl -sf -m 15 "$B/api/layout/state")
assert_num 'occupied cells' "$(echo "$S" | jqp "sum(1 for c in d['cells'] if c['tools'])")" '>' 30
assert_num 'total wip'      "$(echo "$S" | jqp "sum(c['wip'] for c in d['cells'])")"        '>' 0

note 'tool drill-down'
TID=$(echo "$T" | jqp "d['groups'][0]['tools'][0]['id']")
D=$(curl -sf -m 10 "$B/api/tools/$TID")
assert_num "decisions for $TID" "$(echo "$D" | jqp "len(d['recent_decisions'])")" '>' 0

note 'scenario compare (the payload the dashboard actually sends)'
C=$(curl -sf -m 60 -X POST "$B/api/scenario/compare" \
      -H 'Content-Type: application/json' -d '{"tool_overrides":[]}')
assert_num 'baseline assigned' "$(echo "$C" | jqp "d['baseline']['assigned']")" '>' 0

note 'result'
echo "  $pass passed, $fail failed"
(( fail == 0 )) || exit 1
