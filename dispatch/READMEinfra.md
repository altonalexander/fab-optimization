# Infrastructure — one box, real fab topology

Every service here is a **separate machine** in a real deployment. Every
network is a **separate VLAN / firewall zone**. Docker enforces the
boundaries, so they are real in this demo, not just drawn on a slide.

```
┌─ ZONE 0 · equipment ────────── 172.28.0.0/24 · NO EGRESS ─────────────┐
│  amhs-controller (HSMS/TCP :5000)      equipment-sim                  │
│  Vendor software. Rarely patched. TREATED AS HOSTILE.                 │
└──────────────────────────┬────────────────────────────────────────────┘
                           │  amhs-adapter  ◄── the ONLY crossing
                           │  HSMS/SECS-II ──> Envelope
┌─ ZONE 1 · realtime ──────┴─── 172.28.1.0/24 · NO EGRESS ──────────────┐
│  dispatcher     ZeroMQ, fire-and-forget, 1ms budget                   │
│  Pinned CPUs. No disk. No internet. Nothing that adds jitter.         │
└──────────────────────────┬────────────────────────────────────────────┘
                           │  dispatcher (dual-homed 1+2)
┌─ ZONE 2 · data ──────────┴─── 172.28.2.0/24 · NO EGRESS ──────────────┐
│  kafka   mes-producer                                                 │
│  Durable, replayable. A lost LOT_COMPLETE leaks capacity forever.     │
└──────────────────────────┬────────────────────────────────────────────┘
                           │  api  ◄── READ-ONLY, consume-only
┌─ ZONE 3 · enterprise ────┴─── 172.28.3.0/24 · egress OK ──────────────┐
│  ui  :8080  ← the only published port in the entire stack             │
└───────────────────────────────────────────────────────────────────────┘
```

## The invariant that matters

**No service touches both Zone 0 and Zone 3.** A tool is always at least three
hops from a dashboard, and there is no path at all from a browser back to the
dispatcher. A React button must never be able to retune a running fab.

## Commands

```bash
make infra-up     # build and start the whole topology
make verify       # assert the zone invariants hold
make reach        # try to cross the boundaries; they must refuse
make logs
make infra-down
```

`make reach` is the demo. It attempts connections that must fail:
equipment→kafka, equipment→api, dispatcher→equipment, and internet egress from
zones 0–2. Anything reported as `LEAK` is a segmentation break.

## Files

| File | Purpose |
|---|---|
| `docker-compose.yml` | Topology. Networks = firewall zones. |
| `zones.yaml` | Authoritative policy, machine-readable, with invariants |
| `verify-zones.sh` | Asserts invariants against the running stack (CI gate) |
| `reachability-test.sh` | Proves boundaries by attempting to cross them |
| `create-topics.sh` | Kafka provisioning; partition/retention per topic |
| `Dockerfile.*` | One image per zone role, each non-root |

## Mapping to a real fab

Replace three containers and nothing else changes:

- `amhs-controller` → the actual Murata/Daifuku OHT controller IP
- `equipment-sim` → real process tools on the equipment VLAN
- `mes-producer` → the MES lot-tracking feed

`zones.yaml` becomes the VLAN and firewall-rule specification the security team
signs off on. The structure is identical; only the enforcement point moves from
Docker to the switch fabric.

## Not yet real

- Kafka is single-broker with PLAINTEXT. Production: 3 brokers, SASL_SSL,
  `min.insync.replicas=2`, rack awareness.
- CPU pinning is a compose `cpus:` limit. Production: `isolcpus`, tickless
  kernel, planner threads on different cores than the dispatch loop.
- The HSMS client and SECS-II codec are stubs. **Confirm with the AMHS vendor
  whether the controller pushes events or is poll-only before building it** —
  if poll-only, the poll interval becomes the system's latency floor and the
  real-time design needs revisiting.
