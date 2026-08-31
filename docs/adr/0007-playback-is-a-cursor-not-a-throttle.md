# 0007 — Playback is a cursor, not a throttle

**Status:** Proposed, 2026-08-30. The measurements in §2 are real; the replay
cursor in §4 is designed, not built. The pacer fix in §2 and the 800x/1600x
menu entries shipped with this record.

---

## 1. The question

The dashboard has a playback speed — 1x to 400x, now to 1600x — and the
natural reading is that it governs how fast the system runs. It does not,
and should not. Two things are being simulated here and they have different
relationships with the wall clock:

- **The fab.** A discrete-event simulator has no clock of its own; it
  processes events as fast as the CPU allows. Any "speed" is imposed from
  outside by making it wait.
- **The dispatcher's real-time path.** The planner solves on a cycle with a
  budget (≥2 s at 400 lots, per `dispatch/README.md`), the slate is swapped
  atomically, and the fast path answers move requests from the slate in
  ~200 ns. Its constraint is latency against *fab* time: a slate that arrives
  after the lot has already been dispatched by the fallback rule was too
  late, however fast the wall clock was running.

The question is which of these the playback speed should touch, and the
answer is neither: it should touch only what the viewer sees.

## 2. What is throttled today, measured

The **only** throttle in the whole pipeline is the producer's pacer, a
`time.sleep` in `bench/tools/sim_feed.py` between emitted events. Kafka, the
API mirror and the browser are never throttled; they take whatever rate
arrives.

Measured on the live stack (feed → Kafka → Flask mirror → SSE → browser),
LVHM at day 90, 20-core WSL2 host:

| requested | achieved (old pacer) | achieved (anchored pacer) | events/s | feed CPU | API CPU |
|---|---|---|---|---|---|
| 200x | 183x | — | 84 | 6% | 3% |
| 1000x | 791x | **970x** | 383 | 17% | 7% |
| 2000x | 1389x | **1936x** | 650 | 30% | 10% |
| 5000x | — | **4699x** | ~1600 | 54% | — |
| unpaced | **10,060x** | | 4,756 | 97% | 13% |

Two findings:

1. **The pipeline is nowhere near its limit.** At 2000x the feed uses a third
   of one core and the mirror a tenth. The ceiling is the single-threaded
   Python simulator at ~10,000x (≈7 s of CPU per simulated day), not the
   transport.
2. **The old pacer, not capacity, lost 20–30% at ≥1000x.** One `sleep` per
   dispatch event overshoots by ~0.1 ms, and at 1000x events are ~1 ms apart.
   Replaced by an anchored pacer — (wall, sim, speed) fixed at the last speed
   change, sleep only when the accumulated lag exceeds 4 ms, re-anchor rather
   than race after a stall — which holds the requested rate to within 3% up to
   2000x and 6% at 5000x.

So the honest answers to "how fast can we go": 1000–2000x today with room to
spare; ~10,000x is the wall, and it is the simulator's.

## 3. The decision

**Separate simulation speed from playback speed. Run the simulation — and,
once it is in the loop, the dispatcher — as fast as the machine allows.
Playback is a cursor over the recorded stream, owned by the viewer.**

Concretely:

- The producer runs unpaced. Every record it emits is already stamped with
  simulated time (`t=`, `day=`) and a run id; Kafka retains them. The log
  *is* the recording — 0003 and the README already argue this for the
  burndown ("~2.5 GB per 730-day scenario ... the argument for letting the
  log be the recording").
- The dashboard's "now" becomes a **watermark in simulated time** that the
  mirror advances at the viewer's chosen rate (or by *n* steps on request).
  The mirror consumes the event topics up to the watermark and no further;
  the compacted state topics still give it the day-N bootstrap (0003).
- Above ~100x nothing in the network zones is throttled, because nothing
  ever was; below it, the same cursor gives a paced view for watching one
  tool. The producer's `--speed` survives only as a demo convenience.

### The solver's budget is charged in fab time

The part that must *not* be decoupled is the dispatcher's latency, and it
does not depend on playback at all. When the dispatcher runs inside the
simulator (0002), the simulator charges the solver its wall time **as
simulated time**: a solve that takes 2 s of CPU publishes a slate that reaches
the floor 2 fab-seconds after the cycle began, and every move request in
between is answered from the previous slate — or by the fallback rule if there
is none. At 10,000x that is exactly as strict as at 1x. The consequences show
up where they belong, in the KPIs: the optimized-decision share falls when
slates go stale, and the ≥2 s-at-400-lots finding becomes a number the results
tab can contradict.

## 4. What it costs

- **A replay cursor in the API mirror** (`dispatch/api/main.py`). Today the
  consumer subscribes at `latest` and applies everything as it arrives. It
  needs: a watermark; a consumer that reads ahead into a buffer keyed by `t`
  and applies records only up to the watermark; the watermark advanced by a
  timer at the viewer's rate, or stepped by *n* events / *n* fab-minutes from
  the playback control. Seeking backwards is a bootstrap from the compacted
  topics plus a replay from the retained log — slower, and fine.
- **The playback control** (`/api/sim/control`, `bench/.sim_control.json`)
  moves from "tell the producer how fast to sleep" to "tell the mirror how fast
  to advance". The producer stops reading it except for the legacy `--speed`
  demo path.
- **Memory.** The mirror's read-ahead is bounded by how far ahead the producer
  is. Unpaced it is the whole run within minutes; the buffer must therefore be
  a Kafka offset, not an in-memory list — the mirror pauses its consumer when
  the buffer is more than N minutes of fab time ahead of the watermark and
  resumes as the cursor moves. Kafka is the buffer; that is what it is for.
- **The KPI series is unaffected.** It is stamped in fab time by the producer
  and the results tab already reads it as a series, so a run recorded in ten
  minutes reads the same as one watched for a week.

## 5. What this assumes

- **That a record's `t` is enough to place it.** True for every topic today.
  It would break for a record without a fab-time stamp; the mirror already
  drops unstamped burndown points for a related reason (0003 §7).
- **That the retained log covers the replay window.** `fab.lot.events` keeps
  7 days of wall time, `fab.lot.burndown` one day. A run recorded unpaced
  occupies minutes of wall time, so retention is measured in *runs*, not
  hours; watch it once several long runs share a broker.
- **That charging solver wall time as fab time is fair.** It is conservative:
  the production solver would run on a dedicated box, not beside a Python
  simulator on a laptop. The bench (`bench/`) should record the solve-time
  distribution so the charge can be calibrated rather than assumed.

## 6. How to know whether it is right

- **Achieved rate tracks requested rate** at every menu speed, measured as
  the topology tab's "sim clock rate" tile (sim-seconds per wall-second over
  10 s). Today: within 3% to 2000x. Once playback is a cursor, this becomes a
  property of the mirror rather than the producer, and should be exact.
- **Events per fab-hour is constant across speeds** (same tile). It is the
  fab's own event density; if it moves with playback, something is being
  throttled that should not be.
- **It is wrong if** a dispatcher that meets its budget at 1x misses it at
  1000x. That would mean solver latency is being charged in wall time
  somewhere; find it.

## 7. What shipped with this record

- The anchored pacer (§2), so the producer's demo path is honest up to the
  simulator's ceiling.
- **800x and 1600x** in the playback menu. 800x replays a fab-day in 1.8 min
  and 1600x in 54 s — the two rates at which a 30-day comparison run finishes
  over a coffee rather than a shift — and both were measured to hold.

The cursor itself (§4) is the next step, timed with 0002: it is when the
dispatcher runs inside the simulator that charging its latency in fab time
starts to matter.
