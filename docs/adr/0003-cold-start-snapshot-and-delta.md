# 0003 — Cold start: snapshot + delta over compacted topics

**Status:** Accepted, 2026-08-30. Implemented and verified; the warm-up cost
below is measured, not estimated.

---

## 1. The problem

The enterprise mirror holds no state of its own. It rebuilds the fab from the
event stream, which is the right design — the dashboard can die, restart, or
be opened for the first time mid-shift with nobody coordinating.

But a mirror learns that a lot exists only when that lot next **moves**. A lot
sitting in a litho queue announces nothing for hours of simulated time. Worse,
the ~2,000 lots the simulator loads from `WIP.txt` are never *released* — they
are placed directly into WIP — so they emit no event at all until they happen
to dispatch.

A freshly started mirror therefore under-reports WIP for as long as it takes
every lot to touch a tool, and it does so **silently**. A low number looks like
a quiet fab, not a blind observer. That is the failure mode that makes this
worth an ADR rather than a bug fix.

Observed before the fix: with the producer running and 11,556 `LOT_STARTED`
events against 81 completions, the dashboard reported **0 waiting and 4
running**. (Two bugs compounded there; the other was lot identity, fixed
separately.)

## 2. Options considered

**Replay history from the beginning.** Rejected. A year of events is ~15M
records and minutes of consumer startup on every restart, and it reconstructs a
path when the question is a position.

**Query the simulator directly for state.** Rejected. It couples the mirror to
the simulator, so the code path that ships is not the code path that is
tested, and it does not exist at all when the producer is real equipment.

**Keep a snapshot in Postgres.** Rejected. Kafka's compacted topics already
are a durable, keyed, restart-survivable store. Adding a second home for the
same fact is how the two drift. See 0004.

**Snapshot + delta on a compacted topic.** Chosen.

## 3. The decision

The producer states the position of every lot and every tool once, as **keyed**
records on compacted topics — `fab.lot.state`, `fab.tool.state` — and streams
changes from there. The API consumes those topics to completion before tailing
the event topics.

Compaction is what makes this affordable: the log retains exactly one record
per live key, so a consumer reading *from the very beginning* reads the fab,
not its history.

This was not a new mechanism. `create-topics.sh` had declared
`fab.tool.state` compacted since the first commit, with the comment *"so a
restarting consumer gets current state without replaying a week of history"*.
Nothing produced to it or consumed it. The design was already correct and
unwired; this ADR records finishing it.

## 4. What it costs

A discrete-event simulator cannot *start* at day 90 — it has to simulate there.
Measured: **~3 minutes of CPU per 30 simulated days** (a bare loop reached day
93 in 543 s with 2.03M dispatches). So the warm-up is a real, unavoidable,
one-off cost per starting point.

It is therefore cached to `bench/snapshots/`, keyed by dataset, seed,
dispatcher, batch strategy and day. Building takes minutes; replaying from
cache takes **~1.8 seconds**.

```
sim_feed --warmup-days 90    build once, publish, then stream live
sim_feed --warmup-days 90 --snapshot-only    republish from cache
```

## 5. What this assumes

- **That a position is sufficient.** The mirror needs to know where lots are,
  not how they got there. True for the dashboard. Not true for an audit view
  that wants to replay decisions, which needs the event topics as well — they
  are still there, with 7-day and 90-day retention.
- **That the snapshot is consistent.** It is taken between events, at a
  decision point, when the simulator is quiescent. Against real equipment,
  where there is no such instant, this becomes harder and the snapshot would
  need a watermark.
- **That "running" is derivable.** It is read from pending `LotDoneEvent`s in
  the simulator's event queue, which is the only place that fact lives. A
  parallel "currently processing" list would drift from it.
- **That tools can be assumed online.** They are snapshotted as up, and the
  breakdown stream corrects within a cycle. A wrong-but-converging roster beat
  inventing one, because the simulator has no broken flag to read.

## 6. How to know whether it is right

- **WIP conservation.** Waiting + running after bootstrap should equal the
  simulator's actual lot count. Verified: 963 + 1,175 = **2,138** against 2,138.
- **Restart with no producer.** The dashboard should populate from Kafka alone.
  Verified: 2,138 lots and 1,313 tools, no producer running.
- **It is wrong if** bootstrap time grows with run length. That would mean
  compaction is not collapsing keys — either the writes lost their key, or the
  topic was created without `cleanup.policy=compact`. Watch the record count
  read at bootstrap: it should track live lots, not events produced.

Note that the count read at bootstrap can legitimately exceed the live lot
count before the broker has compacted — three snapshots published over time
produced 6,414 records that resolved to 2,138 lots by last-write-wins. That is
correct behaviour, not a leak, but it is also how a genuine leak would first
look, so check the ratio rather than the absolute.

## 7. Consequences

- Two topics are now load-bearing that previously were declared and unused.
  `create-topics.sh` must run before the API, or bootstrap silently finds
  nothing — `dev-up.sh` checks for their presence for this reason.
- The producer must key its state writes. An unkeyed write to a compacted
  topic is retained forever as history, which is precisely the thing being
  avoided.
- The API's bootstrap uses explicit partition assignment rather than
  `subscribe()`. A subscribing consumer returns `None` for seconds during
  group rebalance, which is indistinguishable from an empty topic; the first
  implementation read 0 records from a topic holding 3,451.
