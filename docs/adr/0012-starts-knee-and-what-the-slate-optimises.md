# 0012 — The starts knee, what the slate optimises, and look-ahead coverage

**Status:** Accepted, 2026-09-09. Measurements, and the three decisions they
force. Supplies the numbers 0010 and 0011 asked for; supersedes the
"throughput-oriented pressure tier" idea (batch 1 below) before it was ever
recorded as a plan.

Every run here is LVHM seed 0 from the shared day-90 fifo checkpoint
(`bench/README.md`), headless (`bench/tools/compare.py`), one process per
row. Result files: `bench/results/exp/` (batch 1), `bench/results/starts/`
(starts grid, with its own README), `bench/results/lookahead*/`.

---

## 1. At the scheduled start rate the fab is release-limited

**Batch 1** — 10 days, dispatchers only: fifo, cr, slate at pressure tiers
none / due / full, a new `flow` tier (stronger downstream term, 1.3× batch
fill), slate at a 30 s cycle, greedy solver.

| rule | lots / 10 d | util % | on-time % | tardiness | cycle time |
|---|---:|---:|---:|---:|---:|
| slate:none | 602 | 78.4 | 97.0 | 26.2 | 34.95 |
| fifo | 592 | 79.7 | 96.8 | 15.2 | 36.05 |
| cr | 592 | 80.1 | 100.0 | 0.0 | 36.97 |
| slate (full) | 579 | 78.9 | 98.8 | 2.6 | 35.94 |
| slate:flow | 565 | 78.9 | 98.6 | 2.3 | 35.92 |
| slate, 30 s | 562 | 78.9 | 98.8 | 1.3 | 36.22 |

Throughput within ~7% across every rule, utilization within two points.
`order.txt` releases 57.1 lots/day; the designed load computed from release
intervals × route process times (with metrology `StepPercent` sampling and
wet-bench `STNCAP`) is 67% fab-wide and 83–88% in litho. **No dispatching
rule can lift steady-state throughput above the start rate, or utilization
above the load the starts imply.** What the rule controls is on-time
delivery and cycle time. The `flow` tier is therefore withdrawn as a
throughput lever; it stays selectable for the on-time question only.

## 2. The knee is between 1.00 and 1.05× starts

`compare.py --starts-scale S` compresses the remaining release schedule after
the checkpoint resumes and moves each lot's due date by the same amount.
**Starts grid** — 60 days, S ∈ {1.00, 1.05, 1.10, 1.15, 1.20} × {fifo, cr,
slate}; full table in `bench/results/starts/README.md`.

- At 1.05 every rule's WIP climbs ~9% over the window (≈60 starts/day against
  57–59 completions). From 1.10, WIP grows 3–9 lots/day without settling and
  the queues sit at the litho scanners and tracks (`Litho_FE_111`,
  `Litho_FE_92`, `LithoTrack_FE_115`), as the static load model predicted.
- Fab-wide utilization moves ~1.5 points across the whole grid (80 → 82%).
  Extra starts become queue at the constraint, not busy time elsewhere.
- The static model's headroom (67%) overstates the real one: PM, breakdowns
  and litho at 83–88% leave roughly 3–5% of start headroom.

**120-day confirmation** (`bench/results/starts120/`, table in the starts
README): cr at 1.03× is the sweet spot so far — +0.9 points utilization,
+6 lots/day at the end of the window, 98.6% on-time, WIP drift ~+5% over
120 days; 1.05× costs cr three points of on-time for no more utilization.
Five percent more starts bought 1.4% more completions in 120 days: with a
36-day cycle time the window mostly drains pre-existing WIP. The pre-0012
slate fell from 96.1% to 75.8% on-time across the same scales, which is §3
compounding over a longer window.

**Decision.** 1.03× is the working start rate; 1.05× only if the reworked
slate holds on-time there. Any row on the Results page must carry its
starts scale.

## 3. Under load, critical ratio beats the slate — and why

At 1.05×, end-of-window: cr 59.3 lots/day at 99.3% on-time; slate 57.9 at
95.9% (below its own 98.6% at 1.00×); fifo 50.4 at 96.8%.

The slate minimises `(setup_s + process_s) / urgency` per family: weighted
shortest-processing-time with setup avoidance. That is why it has the
lowest cycle time of the three at 1.00× (35.6 d vs cr 37.0) and flat WIP.
It loses on-time under load for three reasons, all structural:

1. **Its due-date pressure is capped**: ×3 at critical ratio ≤ 1, plus an
   ageing term that takes a week to double. cr's ordering has no cap.
2. **Half the decisions were not the plan's.** Coverage — decision points
   where the plan held a pick for the asking tool — was 47% by the old
   count, and that count credited a token even when its lot was not in the
   queue; the strict count is 33%. Untokened lots were scored by the same
   SPT-flavoured cost.
3. **The assignment has little to assign.** Tools within a SMT2020 family are
   identical (same speed, no dedication, no reticles, one setup time per
   layer, no enforced q-times), so "which lot to which tool" collapses to a
   sort, and the solver's value over a sort key is near zero by construction.

**Decisions.**
- The fallback for untokened lots is **critical ratio** (`--slate-fallback
  cr`, now the default). It ranks by lateness, which is what wins once
  queues form. Tokened lots still rank ahead of it (tiers 0–2 before 3);
  whether a plan should outrank an already-late lot is an objective
  question, left open.
- The due-date term in the urgency is **uncapped**: it now follows the
  critical ratio's own slope below 1 (see `slate_rule._urgency`).
- Further dispatcher tuning waits for the **data overlay** (reticles,
  q-time enforcement, dedication — `docs/NEXT.md` §2). On the pristine
  testbed there is little for an assignment solver to optimise, and the
  segment-scheduler question (NEXT §3) cannot be answered honestly without it.

## 4. Look-ahead: plan what the tool will see, offer only tools that will ask

0010 proposed planning arrivals. Built as `--slate-horizon` (seconds): lots
that will *reach* a family within the horizon — finishing a step now, or
sitting in a route delay step — are planned with the queue, discounted by
distance (five minutes out halves a lot's claim); a tool is offered to the
plan only if it is free or frees within the horizon; coverage counts a
decision only when a token-holder is physically in the queue. **No holds**: a
tool walks its ranking to the first lot that is there.

3 days at 1.05×, cr fallback:

| horizon | coverage (strict) | plan time | wall |
|---|---:|---:|---:|
| 0 s | 32.9% | 205 s | 302 s |
| 900 s (lots only) | 37.0% | 536 s | 662 s |
| 900 s (lots + tools) | **47.0%** | 615 s | 797 s |
| 1800 s | 42.4% | 912 s | 1127 s |

The tool side is what moves it. 30 minutes is worse: a thousand candidates
per rebuild, inbound lots crowding present ones out of the assignment.

**Decisions.** 900 s is the default horizon. The 2.6× wall-clock cost comes
from rescanning the event queue each rebuild; the successor is a
**predictive queue** maintained incrementally on lot starts (next 2–3 tool
sets with ETAs and confidence), which also becomes what the tool page shows
as inbound. After it, **coupled urgency** — a lot carrying the value it
unlocks downstream (the batch it completes, the setup run it continues, the
constraint it keeps fed) — is the experiment that decides whether a segment
scheduler is worth building. Both are specified in `docs/NEXT.md` §1.

## Rematch (2026-09-10, 60 days at 1.05×)

With the §3 and §4 defaults the slate holds **99.2% on-time** at 1.05×
(was 95.9%; cr 99.4%) and keeps the lowest cycle time (36.0 d; cr 37.5),
identical completions, tardiness halved but still above cr's, utilization a
point below cr's. The first overturn condition below is therefore **not**
met on 60 days; the 120-day rows at 1.00 and 1.03 (`bench/results/rematch/`)
are the settled test. Table in the starts README.

## 120-day rematch (2026-09-10)

At 1.00× and 1.03× the reworked slate leads cr on completions (+1.8%,
+1.0%), on-time (99.5% / 99.0% against 99.5% / 98.6%) and cycle time
(1.6–1.7 days shorter), and at 1.00× is the only rule whose WIP falls over
the window; cr keeps ~0.8 points of utilization, which is dwell on tools,
not output. **Operating point: slate with these defaults at 1.03× starts.**
Table in the starts README. The first overturn condition is not met; the
remaining ones stand.

## What would overturn this

- ~~A 120-day row where the reworked slate does not hold on-time within a
  point of cr at 1.03×~~ — tested, holds (99.0 vs 98.6).
- An overlay fab (reticles, dedication, q-times) where the assignment
  solver still adds nothing over a sort → drop CP-SAT from the real-time
  layer and keep it for the segment schedule only.
- WIP settling at 1.05× over 120 days → the knee is at or above 1.05 and
  the grid should be re-run at 1.05–1.10 in 2% steps.
