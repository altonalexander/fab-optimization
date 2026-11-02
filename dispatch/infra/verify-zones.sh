#!/usr/bin/env bash
# Verifies the running stack against zones.yaml invariants.
# This is what turns the diagram into an assertion. Run it in CI.
#
# The cd is load-bearing. INV-4 counts published ports with `docker compose
# ps`, which resolves the compose project from the CURRENT directory -- so run
# from anywhere else it saw no containers, counted zero ports, and reported
# PASS. A check that succeeds precisely when it cannot see anything is worse
# than no check, because it is indistinguishable from one that looked.
set -uo pipefail
cd "$(dirname "$0")"
FAIL=0
ok()   { echo "  PASS  $1"; }
bad()  { echo "  FAIL  $1"; FAIL=1; }

echo "=== Zone invariants ==="

# INV-1: no service on both zone 0 and zone 3
Z0=$(docker network inspect fab-zone0-equipment  -f '{{range .Containers}}{{.Name}} {{end}}' 2>/dev/null)
Z3=$(docker network inspect fab-zone3-enterprise -f '{{range .Containers}}{{.Name}} {{end}}' 2>/dev/null)
BOTH=""
for c in $Z0; do case " $Z3 " in *" $c "*) BOTH="$BOTH $c";; esac; done
[ -z "$BOTH" ] && ok "INV-1 no equipment<->enterprise dual-homing" \
                || bad "INV-1 VIOLATED:$BOTH"

# INV-2: zones 0,1,2 internal
for n in fab-zone0-equipment fab-zone1-realtime fab-zone2-data; do
  I=$(docker network inspect "$n" -f '{{.Internal}}' 2>/dev/null)
  [ "$I" = "true" ] && ok "INV-2 $n has no egress" \
                    || bad "INV-2 $n egress ENABLED"
done

# INV-3: exactly one service bridges zone 0 and zone 1
Z1=$(docker network inspect fab-zone1-realtime -f '{{range .Containers}}{{.Name}} {{end}}' 2>/dev/null)
N=0
for c in $Z0; do case " $Z1 " in *" $c "*) N=$((N+1));; esac; done
[ "$N" -eq 1 ] && ok "INV-3 exactly 1 protocol boundary (amhs-adapter)" \
               || bad "INV-3 found $N services bridging zones 0/1"

# INV-4: one published port
P=$(docker compose ps --format json 2>/dev/null | grep -c '"PublishedPort":[1-9]' || true)
[ "$P" -le 1 ] && ok "INV-4 single published port" \
               || bad "INV-4 $P published ports"

# INV-5: the real-time path does not touch storage. Declared in zones.yaml
# since the beginning and never actually checked here, so the one invariant
# about latency rather than reachability was the one nothing enforced.
Z1=$(docker network inspect fab-zone1-realtime -f '{{range .Containers}}{{.Name}} {{end}}' 2>/dev/null)
RW=""
for c in $Z1; do
  m=$(docker inspect "$c" -f '{{range .Mounts}}{{if .RW}}{{.Destination}} {{end}}{{end}}' 2>/dev/null)
  [ -n "$m" ] && RW="$RW $c:[$m]"
done
[ -z "$RW" ] && ok "INV-5 no writable mounts on zone 1" \
             || bad "INV-5 VIOLATED:$RW"

echo
[ $FAIL -eq 0 ] && echo "ALL INVARIANTS HOLD" || echo "SEGMENTATION VIOLATED"
exit $FAIL
