# 0011 — Downstream-aware dispatch: pushing need back up the route

**Status:** Proposed, 2026-08-30. Design only. Companion to 0010: that
record lets a tool *wait* for something it can see coming; this one lets a
tool *choose* for something far down the line it cannot see from where it
stands.

---

## 1. The question

Every rule the simulator ships with — fifo, cr, lifo, random — decides with
what is in front of the tool: the waiting lots, their ages, their due dates.
None of them asks what the lot will need **next**, or in twenty steps. Yet
that is where the fab's cost is decided:

- the litho families run at **89–93 % busy** with queues of 40–100 lots
  (fifo baseline, run 29; `run_tools`). A minute of litho idle is a minute
  of fab output gone, and litho starves not because of what litho does but
  because of what the tools *feeding* it did an hour earlier;
- a furnace batch needs 3–6 lots of the **same product at the same step**.
  Whether that batch is full when the furnace frees up was decided upstream,
  by whether the wet bench sent those lots together or interleaved them with
  four other products.

A rule that optimises the local queue is, in both cases, optimising the
wrong tool. The need has to travel **backwards** along the route so that
the tool being dispatched now can act on it.

## 2. What travels back

Three signals, each computable from what the simulator already holds:

| signal | what it says | from |
|---|---|---|
| **bottleneck starvation** | the next constraint family on this lot's route (litho, in LVHM) has *b* hours of work queued against a target buffer *B*; the smaller *b/B*, the more this lot is wanted there | `lot.remaining_steps` → the first step whose family is a declared bottleneck; that family's `waiting_lots` summed as process hours |
| **batch affinity** | at the lot's next batch step, *k* partners (same product, same step) are already waiting or arriving; sending this lot now completes a batch, sending it later strands *k* lots | `BATCHMN`/`BATCHMX` on the route step; partners from `waiting_lots` and the look-ahead view of 0010 |
| **due-date slack** | what cr already measures: time to due over work remaining | `deadline_at`, remaining process time |

The dispatch score for a lot at tool *m* is then a weighted sum, and the
weights are the experiment:

```
score = w_slack · slack_norm
      + w_bn    · (1 − b/B)  for the lot's next bottleneck, 0 if none within h steps
      + w_batch · batch_gain  (partners waiting / batch size, if the next batch
                               step is within h steps)
      − w_setup · setup_cost(m, lot)
```

`h` is the look-ahead depth in steps. At `h = 0` this collapses to cr; that
is the control.

This is not new: it is the *starvation avoidance* rule (Glassey & Resende,
1988) and *drum–buffer–rope* (Goldratt) for the bottleneck term, and the
batch-formation upstream sequencing that Fowler and others studied for
furnace areas. What is new here is only that all three ride one score, and
that the simulator makes the experiment cheap.

## 3. Why the simulator can almost do it today

Unlike 0010, nothing structural is missing. The rule receives the lot and
the machine (`dispatcher.py:21,60`); the lot carries `remaining_steps`, each
step its family and batch limits; every family's queue is a list on its
machines. A `pull_ptuple_for_lot` rule can be registered in
`dispatcher_map` from `bench/` — it is a pure function of state the rule
already has access to — and run as a fourth baseline with no change to the
loop.

Two things it needs that are worth building once:

1. **A per-family workload view**, refreshed at each decision point: process
   hours queued per family, and the bottleneck set (declared, e.g. the
   families above 85 % busy in the fifo baseline; or derived live from the
   trailing utilisation the KPI sampler already computes). Cheap; cache per
   `current_time`.
2. **A route index per lot**: for each lot, the step offset of its next
   bottleneck and its next batch step, updated as steps complete. Avoids
   scanning `remaining_steps` (up to 580 entries) on every dispatch.

## 4. Where the real version lives

Rules approximate this with weights; a **planner** does it exactly. The
CP-SAT model (`dispatch/include/fab/solver.hpp`) over a four-hour horizon
already contains the bottleneck and the batch as constraints — tool
capacity, batch firing bounds — and the objective can carry the same three
terms. The slate it publishes is the backward-propagated need, one entry
per (lot, tool); the fast path only reads it. So this record is, like
0010, the rule-shaped special case of what the planner should do, and the
rule is worth building for two reasons only:

- it is a **baseline the planner must beat** — a planner that cannot beat
  a well-tuned pull rule is not paying for its solve time;
- it tells us **which term matters**. If `w_bn` alone recovers most of the
  gain, the planner's objective should be built around litho starvation
  and everything else is a tie-break.

## 5. Measure it

- **Bottleneck starvation time**: hours per day any declared-bottleneck tool
  sat idle with WIP upstream of it. Not sampled today; add to the hourly
  KPI row. This is the number the bottleneck term exists to move.
- **Batch fill ratio**: lots fired per batch over `BATCHMX`, per furnace
  family, per day. Also new; the batch term's number.
- **Setup changes per tool-day**: the cost the setup term pays for; new.
- The existing KPIs — throughput, cycle time, on-time, *where cycle time
  goes* — are the outcome; the three above say *why* it moved.

## 6. The first experiment

Four runs on the results tab, same seed, day 90–120:

1. cr (control, `h = 0`);
2. pull with `w_bn` only, bottleneck set = litho families, `B` = 4 h of work;
3. pull with `w_batch` only, `h` = 6 steps;
4. pull with both.

Success is throughput up at the litho families (their busy share is the
direct read-out) with cycle time not worse, and the batch fill ratio up on
the furnace families. If (2) wins and (3) does nothing, LVHM's furnaces are
not the constraint and the planner's batch machinery is lower priority than
its capacity model — which is worth knowing before building either.

## 7. What this assumes

- **That the bottleneck is stable enough to declare.** In LVHM it is litho,
  by a wide margin, in every run so far. In a fab with a wandering
  bottleneck the set must be derived live from trailing utilisation, which
  the KPI sampler already has.
- **That upstream tools have slack to spend.** Sequencing for downstream
  need costs the upstream tool setup changes and idle; if the upstream tool
  is itself near capacity the score's setup term must dominate, or the
  bottleneck simply moves.
- **That the route ahead is known.** True for the simulator's routes; false
  for the share of lots whose next step depends on a metrology outcome
  (sampling, rework — 0008 §3). The route index treats those as expected
  values.
- **That the discount in 0008 applies.** Free transport and unbounded queues
  make upstream sequencing cheaper here than in a fab where holding a lot
  back occupies a stocker slot. Read the gain with that discount.

## 8. Relation to other records

- 0010 — the hold decision is the local, single-tool form of the same idea;
  the batch term here is what makes a hold there worth taking.
- 0002 / 0006 — the planner and slate are where this belongs in the end;
  the rule is the baseline that tells us what the planner's objective
  should weigh.
- 0008 — the simplifications that inflate the gain; the CQT term would join
  the score once queue-time limits are enforced.
