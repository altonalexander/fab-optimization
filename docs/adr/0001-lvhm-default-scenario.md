# 0001 — LVHM is the default scenario

*Why LVHM, what that assumes, and what would overturn it.*

**Decision (2026-08-30):** every entry point in this repo defaults to the
SMT2020 **LVHM** scenario. HVLM still runs when passed explicitly.

**Status:** the reasoning below is an argument, and measurement has already
gone against part of it. Section 5 records a 60-day probe that **falsifies
assumption 3 and casts serious doubt on assumption 1** — the two mechanisms
section 2 leads with. LVHM remains the default on a narrower argument. Section 4
gives the tests that would settle the rest. Do not quote this document as proof
that LVHM is the right load; quote it as what we assumed and how we agreed to
find out.

---

## 1. What the two scenarios are

SMT2020 (Kopp, Hassoun, Kalir & Mönch, *IEEE Trans. Semiconductor Mfg* 33(4),
2020) ships two fab models. The names describe the product mix:

| | LVHM (low volume, high mix) | HVLM (high volume, low mix) |
|---|---|---|
| Products | 10 | 2 |
| Route lengths | 242–583 steps (mean ~401) | 343 and 583 (mean 463) |
| Station families | 106 | 106 |
| Total tools | 1,313 | 1,443 |
| Release interval, per product | 258.46 min | 51.69 min |
| Wafer starts | ~1,429/day | ~1,429/day |

Both are tuned to the same starts per day, so results are comparable. HVLM's
two routes are byte-identical to LVHM's products 3 and 4 — HVLM is LVHM with
eight products removed and the volume redistributed onto the survivors, then
re-balanced on tool counts (66 of the 106 families differ).

The two are mutually exclusive per run: one `--dataset` argument, one dataset
load per process, and each scenario carries its own `tool.txt.1l`. Running
"both at once" would mean one fab with two contradictory tool sets.

## 2. Why we picked LVHM

> **Read section 5 first.** The batching and setup mechanisms argued here are
> the ones measurement has since undermined. This section is kept as written so
> the original reasoning stays auditable, not because it still stands.

**The argument.** A scenario only tests a dispatcher where the dispatcher's
decisions are binding. `dispatch/include/fab/solver.hpp` decides: which tool
gets which lot, when a batch furnace fires, and which lots share a setup. Those
decisions are only worth making when the corresponding resource is scarce.

Batching is the clearest case. A furnace batch in SMT2020 must be **same
product and same step** — the batching key in the baseline's dispatcher is
`step_name + '_' + part_name` — and holds 3–6 lots (SMT2020 states 75–150
wafers; lots are 25 wafers throughout).

- In **HVLM**, each of 2 products releases 27.9 lots/day. A 5-lot batch of one
  product accumulates in under 5 hours.
- In **LVHM**, each of 10 products releases 5.6 lots/day. The same batch takes
  roughly 21 hours to accumulate.

So in HVLM, batch timing is nearly free and a dispatcher that reasons about it
can show little advantage; in LVHM it is a real decision. Measuring on HVLM
therefore risks a **false negative** — concluding the solver adds nothing when
the load simply never asked it anything.

Release structure sharpens this. All ten LVHM order streams share one interval
and one start, and the loader hardcodes `first_release = 0` for every stream
(`file_instance.py:56`), so they never drift apart. Releases arrive as
perfectly synchronised cohorts of **one lot per product every 258.46 min** — 61
distinct release timestamps in the first 10 days, 10 lots at each. No arriving
cohort can ever form a furnace batch by itself; every batch must be accumulated
across at least three waves, after those lots have diverged through different
routes and queues. That is precisely the state a dispatcher has to reason about.

**The counterweight, stated plainly.** LVHM is also the scenario where a *poor*
dispatcher looks worst, so it is the easiest place to show improvement. Picking
the load on which your system looks best is selection bias whether or not the
mechanism is real. Two guards: (a) report HVLM alongside LVHM in any published
comparison, even though LVHM is the default; (b) never tune parameters on LVHM
and report only LVHM.

## 3. What this assumes

Each of these is load-bearing. If one is false, the choice weakens or fails.

1. **The dispatcher's advantage comes mainly from batching and setup
   avoidance,** rather than from raw tool assignment under capacity pressure.
   *If false:* the scarce resource is capacity, not batch timing, and HVLM —
   with its higher per-tool load — is at least as good a test. **Section 5 has
   evidence pointing this way.**
2. **Batch-forming delay is a material share of LVHM cycle time.** If lots
   spend most of their queue time waiting for a tool rather than for batch
   partners, the 21-hour accumulation figure is arithmetic without consequence.
3. **Setups are frequent enough in LVHM to matter.** Ten products sharing tools
   should thrash setups more than two. **Falsified under FIFO — see section 5.**
4. **The two scenarios can rank dispatch policies differently.** If FIFO, CR and
   the solver rank identically on both, scenario choice is irrelevant and this
   decision is harmless but also pointless.
5. **~1,429 starts/day is genuinely comparable across the two.** Taken from the
   release definitions, not from measured throughput. Neither scenario is
   necessarily *feasible* at that rate; if LVHM is saturated and HVLM is not,
   we are comparing a fab that is falling behind against one that is not.
6. **The simulator's batching behaviour reflects the dataset's intent.** The
   baseline's `batch_min` is a soft preference, not a hard gate — under-minimum
   groups sort last (`greedy.py:91`) but can still run. A furnace can therefore
   fire below `BATCHMN` when nothing better waits.

**Not assumed, because it has no data:** reticle exclusivity. SMT2020 contains
no reticle model at all. The solver's reticle constraint is untested on this
load and would be untested on HVLM too. Any claim about reticle contention on
SMT2020 is unfounded regardless of scenario.

## 4. How to know whether it is right

Concrete, runnable, with a stated threshold. Each is a way to be *wrong*, not a
way to confirm.

**T1 — Does batching policy actually change anything?** The strongest single
test, because it targets assumption 1 directly.

```bash
cd baselines/pyscfabsim
for s in Min Max Demand; do
  .venv/bin/python main.py --days 730 --dispatcher fifo --seed 0 --batch_strat $s
done
```

*Overturns LVHM if:* cycle time and throughput move by only a few percent
across the three strategies. Batching policy would then not be load-bearing,
and the central reason for preferring LVHM would be gone.

**T2 — Is setup time actually incurred?**

```bash
.venv/bin/python ../../bench/tools/tool_probe.py --dataset LVHM --days 60 --top 60
```

*Overturns assumption 3 if:* the `setup` column is ~0% across essentially all
tools. Then "10 products thrash setups" is a story the data does not support.

**T3 — What is the binding constraint?** In the same probe, `block` is idle
time *with lots queued* — that column, not `busy`, identifies a constraint. If
the blocked tools are Diffusion (furnaces waiting for batch partners), the
batching rationale holds. If they are Litho, the fab is capacity-bound at
lithography and LVHM is testing assignment, not batching.

**T4 — Can the scenario discriminate policies at all?**

```bash
PYSCFABSIM_DAYS=730 PYSCFABSIM_SEEDS=0,1,2 \
PYSCFABSIM_MATRIX=LVHM:fifo,LVHM:cr,HVLM:fifo,HVLM:cr \
  ./reproduce_dispatcher_experiments.sh
```

*Overturns assumption 4 if:* the FIFO-vs-CR gap is within seed noise on LVHM,
or the policy ranking is identical on both scenarios. The first means LVHM
cannot discriminate; the second means the choice does not matter.

**T5 — Is LVHM saturated?** Compare achieved throughput against the ~1,429
wafers/day released, over a 730-day run. *Overturns assumption 5 if:* LVHM's
WIP grows without bound while HVLM's is stable. We would then be comparing an
overloaded fab to a healthy one, and any cycle-time difference would be an
artifact of that, not of dispatching.

**Runs must exceed 365 days.** Shorter runs include the fill-up transient with
no warm-up reset, and are not comparable to the 730-day numbers. Every figure
in section 5 is from a short run and is therefore indicative only.

## 5. Evidence in hand

Thin, and honestly reported.

**Verified, from the dataset files:** the product counts, route lengths, tool
counts, release intervals and ~1,429 starts/day in section 1; the 3–6 lot batch
bounds; the same-product-and-step batching key; the synchronised release
cohorts. These are arithmetic over `order.txt`, `tool.txt.1l` and the route
files, not simulation output.

**From a 60-day LVHM probe (FIFO, seed 0, top 60 tools) — transient included:**

| | |
|---|---|
| Busiest families | LithoMet (mean 91.1% busy, n=17), Litho (89.5%, n=18), DE (89.0%, n=20), Diffusion (88.3%, n=3) |
| Setup | max **5.6%**, and only **2 of 60** tools above 1% |
| Block (idle with lots queued) | max 8.3%, only 3 of 60 above 1% |
| Queues | `Litho_BE_110` q_avg ~58, q_max ~143 |

**T2 comes back negative: assumption 3 is falsified.** Setup time is essentially
not incurred in LVHM under FIFO. The "ten products thrash setups more than two"
story is not supported by the data and should not be repeated.

**T3 comes back on its second branch, and assumption 1 is in serious doubt.**
The binding constraint is lithography and litho-metrology capacity, not furnaces
waiting for batch partners. Tools are near-saturated rather than blocked, which
is the signature of a capacity-bound fab rather than a synchronisation-bound one.

Read honestly, that removes *both* mechanisms section 2 leads with. What survives
of the case for LVHM is narrower and should be stated as such: ten products give
a more diverse assignment problem than two, and batch formation still takes ~21
hours per product whether or not furnaces are currently the tightest constraint.
That is a weaker argument than the one originally made.

Three caveats before treating this as settled. The run is 60 days, under the
365-day threshold, so it includes the fill-up transient. It is FIFO only — a
dispatcher that deliberately groups by setup would change the setup column,
which is precisely what we would be measuring. And it covers the 60 busiest
tools, so it says little about the other ~850, including most furnaces (only 3
Diffusion tools appear in the sample).

**Open action:** re-run T1 (`--batch_strat Min|Max|Demand`) and T2 at 730 days
before publishing any comparison that leans on LVHM. If T1 also comes back flat,
the batching rationale is gone entirely and this decision must be re-argued on
assignment diversity alone — or reversed.

## 6. The tool master derived from this decision

`dispatch/config/fab_tools.json` is now a symlink to `fab_tools_lvhm.json`,
generated by `dispatch/tools/smt2020_tool_master.py` from the LVHM files:
**913 tools across 105 station families** (10 BATCH_FURNACE, 13 METROLOGY, 11
LITHO_SCANNER, 71 SINGLE_WAFER).

It previously described "FAB1" — 12 invented tools with invented recipes. The
tools page, the demo lot pool and `/api/scenario/compare` all described a fab
that did not exist while the live feed showed LVHM's real tools.

Tool kinds are derived from behaviour in the data (BATCHMN/BATCHMX for
furnaces, StepPercent for sampled metrology), not from keywords in family
names — LVHM's names are abbreviated (`DefMEt_FE_118`, `WE_FE_84`) and match no
keyword rule, so name-based classification would have labelled the entire fab
SINGLE_WAFER and quietly removed both batching and sampling.

SMT2020's 400 `Delay_*` queue-time pseudo-tools are excluded, matching
`tool_probe.py`. They are not equipment; including them would put 400 phantom
tools on the tools page.

Its approximations are listed in the script header and are part of what this
document commits us to re-examining: no reticle model, a single `changeover_s`
standing in for SMT2020's setup matrix with minimum run lengths, and process
times averaged over the steps routed to each family.

## 7. If LVHM turns out to be wrong

The cost of reversing is low by construction, which is part of why standardising
now is defensible. Scenario selection is one default per entry point plus one
symlink:

- `baselines/pyscfabsim/simulation/greedy.py` — `--dataset` default
- `baselines/pyscfabsim/greedy_runner.py` — `PYSCFABSIM_MATRIX` default
- `baselines/pyscfabsim/exp_set_gen.py` — generated experiment set
- `bench/tools/sim_feed.py`, `bench/tools/tool_probe.py` — `--dataset` defaults
- `dispatch/config/fab_tools.json` — symlink; regenerate with
  `python3 dispatch/tools/smt2020_tool_master.py --scenario HVLM`

Record the reversal here with the measurement that forced it, rather than
editing section 2 to make the original choice look better than it was.
