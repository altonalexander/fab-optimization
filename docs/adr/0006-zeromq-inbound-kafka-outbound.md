# 0006 — ZeroMQ inbound, Kafka outbound: the transport split

**Status:** Accepted, 2026-08-30. The ZMQ side is built and in use
(`amhs_adapter_main.cpp`); the Kafka bodies in `transport.hpp` are sketched
against librdkafka and not yet compiled. See §5.

---

## 1. The question

`README.md`'s architecture diagram shows two transports on the two edges of the
realtime zone: ZeroMQ carrying equipment → ingest, Kafka carrying the realtime
zone → the data zone. A reader who has just been told that Kafka is the system
of record for live state (0004) reasonably asks the obvious question:

> Why isn't the whole pipeline on Kafka? The only thing that has to be fast is
> the move request and the slate lookup.

That question is correct about the fast path and wrong about the budget, and
the answer has been sitting in a header comment in `zmq_transport.hpp` rather
than here. 0004 drew the line between two *stores*. This draws the line between
two *transports*, which is a different line in a different place.

## 2. Options considered

**Kafka everywhere, including equipment → ingest.** One transport, one
operational story, one set of failure modes to learn. Rejected for the four
reasons in §3.

**ZeroMQ everywhere, including the dashboard feed.** Also one transport, and
the fast one. Rejected because the dashboard is a mirror that must survive
being started mid-shift (0003), and ZMQ is lossy and unreplayable by design. A
PUB socket has no answer to "what did I miss while I was down", which is the
entire cold-start problem.

**Shared memory / in-process for the inbound edge.** Lower latency still, and
genuinely tempting. Rejected because the AMHS adapter is in the equipment zone
and the dispatcher is in the realtime zone; putting them in one address space
collapses a segmentation boundary that `zones.yaml` exists to enforce. ZMQ over
`ipc://` when co-located gets most of the win without that.

**Direct TCP, no library.** Rejected as re-implementing ZMQ's socket patterns
badly. DEALER/ROUTER's async correlation is exactly the shape of an HSMS
transaction and is not trivial to rebuild.

## 3. The decision

**ZeroMQ on the inbound edge. Kafka on the outbound edge. Neither crosses.**

Four reasons, in the order they matter:

**The fast thing is not only the lookup.** A ~200 ns slate lookup buys nothing
if the slate was built from a `FabState` that is stale. The budget that matters
is tool event → decision, end to end, and `zones.yaml` states it as
`latency_budget_ms: 1` for the realtime zone. Ingest is inside that budget even
though ingest is not itself "the fast path". The sharpest case is the fallback:
`Slate::lookup` switches to the alternate tool when the primary went down
*mid-cycle*, and that only works if the down event reached `FabState`
promptly. A broker hop plus `linger.ms` plus a consumer poll loop makes the
fallback fire late — in precisely the situation it exists for.

**A broker on the control path is an availability coupling.** With ZMQ, the
adapter and the dispatcher are peers: no third process has to be alive for the
fab to keep dispatching. Route the inbound edge through Kafka and a broker
restart, a leader election, or a consumer-group rebalance stops dispatch. Today
Kafka can die and the fab keeps running; only the dashboard goes blind. That is
the same asymmetry 0004 §5 applies to Postgres, and it is the test of whether
the line is in the right place.

**Ordering and single-writer semantics.** `FabState` is a single writer by
construction. Kafka guarantees order only per partition, and a rebalance gives
you redelivery and cross-partition reordering — the exact failure mode a
single-writer state machine is built to avoid. Avoiding it means pinning to one
partition and one consumer, at which point a distributed log has been paid for
and a point-to-point pipe delivered.

**Zone enforcement.** The zones are separate networks, and the data network is
`internal: true`. Putting the equipment edge on Kafka routes tool traffic
through the data zone and breaks the property `verify-zones.sh` checks: that no
service touches both the tools and a browser.

And the corollary that makes the split coherent rather than merely fast:
**move commands are lossy by design.** If a command is dropped the vehicle
re-requests. Delivery guarantees on that path buy latency, and a stale move
command is worse than a missing one. Durability is placed deliberately
*downstream* of the realtime zone, in the compacted topics, where fsync latency
is not inside anyone's decision loop.

## 4. What this assumes

**That the inbound message rate stays low enough for one ZMQ IO thread.**
`ZmqContext` pins exactly one, on the grounds that it is plenty at our rate.
Untested at fab scale.

**That re-request is genuinely cheap.** The lossy-by-design argument rests
entirely on the vehicle re-requesting on drop. If a real AMHS controller does
not, or backs off slowly, the argument inverts and the inbound edge needs
delivery guarantees after all. This is the weakest assumption here and it is
inherited from the equipment protocol, not chosen.

**That the planner never needs a wire.** The planner reads `FabState`
in-process, so the 30–60 s cycle has no transport decision to make. This is
worth stating because it is the part of the pipeline the original question is
right about: CP-SAT on a 30–60 s cycle would be perfectly happy consuming from
Kafka. The question only bites for ingest — and ingest is the part that cannot
afford a broker. If the planner is ever split into its own process, this ADR
does not decide its transport, and Kafka is the likely correct answer there.

**That the two transports stay cheap to operate together.** Two transports is
two sets of failure modes. That cost is real and is being accepted, not
denied.

## 5. Evidence in hand — including evidence against

For: the split is implemented on the ZMQ side and running.
`amhs_adapter_main.cpp` binds PUB on 5562 and SUB on 5561 northbound into the
realtime zone. `zones.yaml` declares `protocols: [ZeroMQ]` for that zone with a
1 ms budget, and `verify-zones.sh` checks the segmentation.

Against, and it should be said plainly: **the Kafka half is not compiled.**
`transport.hpp` carries `KafkaProducer`/`KafkaConsumer` bodies behind
`FAB_HAVE_RDKAFKA` that have never been built, and the default path is
`InMemoryBus`. So the outbound edge of this decision is asserted, not
demonstrated. 0005 verified the broker and the wire format round-trip
independently, which is not the same as this code having spoken to it.

Also against: no measurement exists of what Kafka on the inbound edge would
actually cost. The latency argument in §3 is a mechanism argument — broker hop,
batching, poll loop — not a measured p99. It is very likely right and it is not
evidence.

## 6. How to know whether it is right

- **It is wrong if** a measured inbound p99 through Kafka comes in comfortably
  under the 1 ms zone budget. Then the first reason collapses and only the
  availability and ordering arguments remain, which are weaker on their own.
- **It is wrong if** the drop rate on the ZMQ edge turns out to be high enough
  that vehicle re-requests are a visible share of inbound traffic.
  Lossy-by-design is only correct while loss is rare.
- **It is wrong if** anything in the realtime zone ever needs to replay. Replay
  is Kafka's job and ZMQ cannot do it; needing it inbound means the boundary is
  misplaced.
- **It is right if** the dispatcher survives a full Kafka outage with no
  dispatch impact, and the dashboard recovers from the compacted topics
  afterwards with no coordination. That is one experiment and it has not been
  run.

The last one is the decisive one, and it cannot be run until the Kafka bodies
compile.

## 7. Consequences

- Two transports to operate, monitor and understand. Accepted cost.
- `zmq_transport.hpp`'s header comment is now a summary of this ADR rather than
  the only home for the reasoning. Keep it short; if the two disagree, this
  file wins.
- The realtime zone must never gain a Kafka dependency, and the data zone must
  never gain a ZMQ one. `verify-zones.sh` checks network membership, not
  protocol use — so this particular invariant is currently enforced by review,
  which is worth fixing.
- Anything requiring durability must be published outbound. A fact that exists
  only on the ZMQ edge does not survive a restart and must not be treated as
  state.
