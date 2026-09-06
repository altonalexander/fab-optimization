#!/usr/bin/env bash
# Proves the boundaries are REAL, not decorative. Each of these MUST fail.
# This is the demo: run it in front of your boss.
set -u
echo "=== Reachability: these connections MUST be refused ==="
t() { # container target description
  if docker compose exec -T "$1" timeout 3 bash -c "</dev/tcp/${2}" 2>/dev/null; then
    echo "  LEAK  $3  <-- SEGMENTATION BROKEN"
  else
    echo "  BLOCKED  $3"
  fi
}
t amhs-controller kafka/9092        "equipment -> kafka"
t amhs-controller api/8000          "equipment -> api"
t dispatcher      amhs-controller/5000 "dispatcher -> equipment (adapter must mediate)"
echo
echo "=== Positive control: this one MUST connect ==="
if docker compose exec -T api timeout 3 bash -c "</dev/tcp/kafka/9092" 2>/dev/null; then
  echo "  OK  api -> kafka"
else
  echo "  BROKEN  api -> kafka  <-- the mirror cannot see the fab"
fi
echo
echo "=== Egress: zones 0-2 must not reach the internet ==="
for s in amhs-controller dispatcher kafka; do
  if docker compose exec -T $s timeout 3 bash -c "</dev/tcp/1.1.1.1/443" 2>/dev/null; then
    echo "  LEAK  $s has internet egress"
  else
    echo "  BLOCKED  $s no egress"
  fi
done
