# 0010 — Look-ahead dispatch: the hold decision

**Status:** Proposed, 2026-08-30. Design only; nothing in this record is
built. It exists so the idea is not lost and so the first experiment is
specified before the dispatcher (0002) is in the loop.

---

## 1. The question

When a tool frees up, the simulator asks the dispatching rule *"which of
the lots already waiting for this tool goes next?"* — and the rule must
answer with one of them. It cannot say *"none, yet"*. So a tool never idles
on purpose, even when a better lot is five minutes away.

The prompt for this record was a specific case: a lot finishing a **delay
step** (the route's mandatory holds after defect metrology and wet etch —
2–4 h, see 0008 §2) is a perfectly predictable arrival. Should the next tool
wait for it? Sometimes, and the question generalises: every in-progress step
in the simulator is a `LotDoneEvent` with a known timestamp, so *every*
arrival within the next few hours is known. A real MES knows nearly as much.

## 2. When waiting pays

The trade is idle time on **this** tool against a decision that is better
for the **fab**. It pays when the arriving lot buys something the waiting
lots cannot:

| case | what waiting buys | data that says how much |
|---|---|---|
| setup avoidance | the waiting lots need a setup change; the arriving lot has the current setup | `setup.txt` (7–12 min on DE families, more on implant) plus the min-run-length commitment after a change |
| batch filling | a furnace has 3 of a 4-lot batch; the 4th is 20 min out. Firing now wastes a quarter of a multi-hour batch | `BATCHMN`/`BATCHMX`, batch process times |
| hot lots | a priority-20 lot is about to arrive; starting a 3 h regular lot now delays it 3 h | `order.txt` hot-lot stream, priorities |
| queue-time limits | the arriving lot has a hard CQT window, the waiting one does not | `CQT` columns — once enforced (0008 §2) |

And when it does not: on a bottleneck (LVHM litho track and litho metrology
run at 96–97 % busy in the fifo baseline, per the results tab) a minute
idle is a minute of fab throughput gone, and "never wait" is close to
right. The rule is therefore *hold only when this tool has slack and the
gain exceeds the idle*, which is a judgement a planner with a horizon can
make and a rule like fifo or cr cannot express at all.

## 3. Why the simulator cannot do it today

Two properties recorded in 0008 §3:

- Dispatch is **machine-centric and reactive**. `get_lots_to_dispatch_by_machine`
  is called when a machine frees up; returning `None` removes the machine
  from `usable_machines` until some other event happens to free it again.
  There is no notion of *"come back at t"*.
- The rule **sees no arrivals**. It is handed the machine and its
  `waiting_lots`; the event queue, which knows every in-flight completion
  time, is not part of its input.

## 4. The design

Three pieces, all in `bench/` so the vendored simulator stays read-only
(`UPSTREAM.md`).

### 4.1 A look-ahead view

Before each dispatch, for the freed tool's family, build the list of lots
that will arrive within a horizon *H*: scan the pending `LotDoneEvent`s
whose lot's **next** step is served by this family, taking the event
timestamp plus the transport draw's mean (`fromto.txt`: 7.5 min) as the
predicted arrival. Cost: a scan of the event queue per dispatch; cache per
decision point if it shows up in the profile.

This is legitimately what an MES knows, but not perfectly. Give the view a
**noise knob** — σ on the predicted arrival, default 0 for the sim's own
truth — so a run can state how much foresight it assumed. A hold rule that
only wins at σ = 0 is not a rule.

### 4.2 A hold decision

Let the rule return `Hold(until_t, reason)` instead of a lot. The run loop
(`sim_runner.run`) then:

1. removes the machine from `usable_machines` (the existing `None` path);
2. schedules a `WakeMachineEvent(t)` — a small `Event` subclass whose
   `handle(instance)` calls `instance.free_up_machines([m])` — at
   `until_t`, or at the predicted arrival, whichever is earlier;
3. records the decision with `src=hold:<reason>` on the decision stream, so
   it counts as an optimised decision (0007 §3, the optimized-decision KPI).

The vendored `Event`, `EventQueue.add_event` and `free_up_machines` already
support all of this; the loop change is on the order of thirty lines. If
the awaited lot arrives early, its own `LotDoneEvent` frees the machine
through the normal path and the wake event finds nothing to do.

### 4.3 Measure it

- **Cycle-time split** gains a bucket, *held for arrival*, beside queue /
  batch-wait / processing / delay: the lot-hours other lots spent waiting
  while a tool idled on purpose. It is the cost side of the ledger and must
  be visible next to the gain.
- **Tool books** already carry busy %; a hold shows up as lower busy on the
  tools it was applied to, which is the check that it stayed off the
  bottleneck.
- **Decision source** `hold:*` per tool and reason, so the results tab can
  say *how often* it held and *for what*.
- **Setup changes per tool-day** and **batch fill ratio** — neither is
  sampled today, both are the direct effect of the two most promising hold
  reasons, and both are cheap to add to the hourly KPI sample.

## 5. The first experiment

Narrow and safe, as a third baseline run beside fifo and cr on the results
tab:

- hold only for **setup match** and **batch completion**;
- only on tools whose trailing busy share is below ~85 %;
- only for arrivals predicted within **15 min**;
- σ = 0 first, then σ = 5 min, to see whether the gain survives uncertainty.

Success is a lower cycle time at equal or higher throughput against the
fifo baseline, with the *held for arrival* bucket small and the bottleneck
tools' busy share unchanged. Failure is just as useful: it says the LVHM
routes leave too little slack for holds, and the planner should not bother.

## 6. What this assumes, and what would change it

- **That arrivals are predictable to within the horizon.** True in the
  simulator; true enough in a fab with an MES and a modelled AMHS. False for
  lots whose next step depends on a metrology result — sampling (0008 §3)
  makes the *next step* itself uncertain for a share of lots, which the
  look-ahead must treat as "may arrive".
- **That idling a non-bottleneck is free.** It is free for throughput; it is
  not free for the lots that waited. Hence the bucket in §4.3.
- **That this is the planner's job, not a rule's.** A slate (0006) can carry
  *"reserve tool X for lot Y at t"* entries — this is the predictive half of
  predictive dispatch, and the hold decision here is its single-tool,
  single-lot special case. If the planner lands first, this record's
  experiment becomes a regression test for it rather than a feature.

## 7. Relation to other records

- 0002 — the dispatcher runs inside PySCFabSim; the hold hook is the first
  thing that dispatcher needs that the rules did not.
- 0007 — the solver's latency is charged in fab time; a hold is a decision
  with a deadline, and a late slate should lose the hold, not extend it.
- 0008 — the simplifications that make holds *cheaper* here than in a fab
  (free transport, unbounded queues); read the gain with that discount.
