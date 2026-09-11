# Next steps

One list, ordered by what gates what. The C++ placeholders stay in
[`dispatch/README.md`](../dispatch/README.md#placeholders-by-priority) and the
benchmark's open items in [`bench/README.md`](../bench/README.md#still-open);
this page is the programme they hang off. Dates are when an item was written.

## 1. Find the knee (running, 2026-09-09)

The dispatcher cannot raise throughput or utilization at the current start
rate: batch 1 (`bench/results/exp/`, 10 days from the day-90 checkpoint)
spread every rule within ~7% throughput and two points of utilization, because
`order.txt` releases 57 lots/day and the designed load is 67% fab-wide, 83–88%
in litho. Starts are the lever.

- [x] **Starts grid** — `bench/results/starts/README.md`. Knee between 1.00
      and 1.05 (not 1.10): WIP drifts +9% per 60 days at 1.05 and grows
      without settling from 1.10; litho scanners and tracks hold the queues.
      Fab-wide utilization moves ~1.5 points across the grid. Under load `cr`
      beats `slate` on throughput and on-time.
- [x] **120-day confirmation** (`bench/results/starts120/`, `rematch/`):
      operating point is the reworked slate at 1.03× starts — +1.0%
      completions over cr, 99.0% on-time, cycle time 1.6 d shorter, WIP
      drift ~+5% per 120 days. Details in ADR 0012.
- [x] **Slate fallback = cr** — default since ADR 0012. Tokened lots still
      rank ahead of the fallback (tier 0-2 before 3), which is the next thing
      to question.
- [ ] **Look-ahead** (`--slate-horizon`, built 2026-09-09): lots arriving
      within the horizon are planned with the queue, discounted by distance;
      tools are offered only if free or freeing within it; coverage counts a
      decision only when a token-holder is physically present. One tool set
      deep, read off the event queue each rebuild (2-3x wall clock).
- [ ] **Predictive queue** (successor to the above): on every lot start,
      project the next 2-3 tool sets with ETAs (first exact, later ones from
      family load, with confidence), maintained incrementally; the family's
      planning queue = true queue + inbound. Consumed by the planner (never as
      present), by the decision ranking (walk to the first lot that is here),
      and by the tool page as ghosted inbound FOUPs with ETAs. Same family-load
      estimate as the downstream term.
- [ ] **Coupled urgency** (after the predictive queue): a lot's urgency
      also carries what it unlocks one or two tool sets ahead — the batch it
      completes (inherits the urgency of the lots waiting in that group), the
      setup run it continues (the changeover it saves), the constraint it
      keeps fed (the idle minutes it prevents). Conditioned on ETA confidence
      and on arriving before the group fires without it; modest weight, 1-2
      sets deep. This is the experiment that decides the segment scheduler:
      if it captures most of the gain at 1.05x, the scheduler stays unbuilt.
- [x] **Slate due term uncapped** (ADR 0012): follows critical ratio's slope
      below 1. Re-test at 1.05 with the 120-day rows.
- [ ] **Loss analysis at the knee** — from the slate rows at 1.10 / 1.15: per
      litho family, share of the window in setup, share idle with work one
      step upstream, mean batch fill in diffusion. This is the ceiling for any
      scheduler and decides §3. Under ~3%: do not build it.
- [x] Feed default is `slate` with the 0012 defaults (`scripts/dev-up.sh`).
- [ ] Starts scale on the live feed: `sim_feed.py` has no `--starts-scale`
      yet; the dashboard runs at 1.00×. Add it so the Results page can show
      the 1.03× operating point live.
- [ ] Record the starts scale in the run store and on the Results page, so a
      row at 1.10x is never laid over one at 1.00x unlabelled.

## 2. Make the fab worth scheduling (data overlay)

SMT2020 as simulated has no reticles, ignores the queue-time limits it parses
(ADR 0008), has no tool dedication, and gives litho tracks a flat per-layer
setup time. Add these as an **overlay** the instance builder applies by flag —
never edit the testbed; the pristine LVHM stays the baseline row and every
rule runs on the same augmented fab.

**Tool dedication is planned in full in ADR 0013** (2026-09-10) and is the
hand-off item: it is written to be picked up by another agent on a machine
with more cores, since the rows are embarrassingly parallel and each `slate`
row is ~40 min per 30 days. Order of work is 1, then 2+3, 4, 5, then 6+7.
Qualification is master data (on the tool, loaded once), not lot state on
the wire; see 0013 §2 before adding anything to `LOT_READY`.

- [x] 0013 §3.3 — one `instance.eligible(lot, machine)` predicate in the
      vendored simulator, default = existing dedication check. Gate held:
      pristine `slate-cr` still reproduces `cr` at 47,149 decisions,
      fp `8d77d45c4c2654a3`. The predicate is inert on the pristine fab.
- [x] 0013 §3.1 — overlay mechanism: `data/smt2020/overlays/<name>/`
      (`qualification.tsv` + `provenance.json`), `--overlay` on `compare.py`
      and `sim_feed.py`, day-90 checkpoint keyed by overlay hash.
- [x] 0013 §3.2 — `bench/tools/gen_overlay.py`. **The capacity check refuses
      the fractions this page asked for.** 0.50 all-scope is rejected on
      eight families (`Litho_FE_98` 79.2% → 117.6% effective), and the
      balanced frontier is between 0.60 (refused) and 0.65 (written). What
      exists is `dedication-litho-70`, `dedication-all-70`,
      `dedication-all-80`, plus `dedication-all-65` (hardest feasible) and
      `dedication-skew-70` (the `--skew` variant). On SMT2020 at the 1.03×
      operating point you cannot make dedication much harder without
      deleting capacity, which is itself a finding about the testbed.
- [x] 0013 §3.4 — `qualified_parts` on the C-ABI tool struct, filled once at
      `set_tools`; `FamilyTool::evaluate` rejects `RecipeNotQualified`;
      `test_main.cpp` case (97/97 pass); `slate-cr` gate under all three
      overlays (45,882 / 45,281 / 45,138 decisions, each matching `cr`).
- [x] 0013 §3.5 — KPI: family hours idle with qualified WIP waiting, on every
      row as `idleQ` (and `idleF` unfiltered), with overlay name/hash and
      starts scale in the result JSON.
- [ ] 0013 §3.6 — **30-day screen done and negative**
      (`bench/results/dedication/README.md`): the slate − cr gap does not
      grow with dedication, it runs backwards — largest on pristine at 1.03×
      (+9.6%) and on the least dedicated overlay (`all-80`, +12.8%), negative
      on the most dedicated (`all-70`, −1.3%) — and `idleQ`, the KPI built
      for this, is 32–71 t·h/d worse for the slate in all eight cells.
      Coverage rose (45.4% → 48.7–51.7%) where §3.5 said it would fall.
      Still to run: 120 days at 1.03× on the most separating overlay
      (seeds 0, 1) and the pressure ablation. The 30-day window cannot carry
      the throughput column — ~36-day cycle time, and ADR 0012 measured
      +1.0% over 120 days where the screen shows +9.6%.
- [ ] 0013 §7 — status update and `summary.md` §7.4 with pristine and
      dedicated tables side by side; decide ADR 0012's overturn condition.
      On the screen alone the condition is **not** met, but the honest
      reading is *untested* rather than *refuted*: these matrices are mild
      enough that `fifo` holds 98–99% on-time on every fab. The harder
      `all-65` / `skew-70` rows are the tiebreak before the reticle branch.
- [ ] **Reticles** per (part, litho layer): exclusive across scanners, transport
      delay between them, two copies for high-volume parts. Simulator gets a
      reticle resource; `LOT_READY` gains a reticle id; the C++ planner's
      reticle fields start to bind; the litho scene draws the library. Next
      overlay if dedication alone does not separate the rules (0013 §7).
- [ ] **Queue-time enforcement** (CQT columns): scrap or rework on violation;
      un-inert the planner's q-time term (`QTIME_INERT` in `slate_rule.py`).
- [ ] Sequence-dependent track setups — only if the loss analysis names
      setups as a material loss.
- [ ] Rerun the starts grid on the overlay; same page, with and without.

## 3. Segment scheduler (decision gated on §1 and §2)

A CP-SAT interval model over the litho cell (track → scanner → track → litho
metrology, ~120 tools, 4–6 h horizon): no-overlap per tool, setup-dependent
transitions, batch tools as cumulative resources, weighted tardiness +
bottleneck feeding. Rolling horizon (re-solve every 10 fab-minutes or on a
breakdown inside the segment, 15-minute freeze), feeding the slate planned
tool **and** planned start; the slate stays the real-time layer and the
fallback. Coverage becomes the schedule-adherence metric. Fab-wide scheduling
is out: too large, stale before it returns, and unnecessary below the knee.

## 4. Dispatcher hygiene

- [ ] `slate:flow` tier (2026-09-09): stronger downstream term, 1.3× batch
      fill. Flat at 1.0x; keep only if it earns its row at the knee, else drop.
- [ ] Coverage is ~47%: half the decisions are the solver-consistent fallback.
      Parallel per-family solves (bench/README) or a shorter cycle without the
      wall-clock cost.
- [ ] Post-switch stamping: `tool_setup` / `setup_match` in the decision
      record are taken after the setup change, so the chosen lot always reads
      as matching. Stamp before the switch (the tuple's `setup_s` is the
      truth today; the UI works around it).
- [ ] Minimum-run commitments (`min_runs_left`, implant only) on the wire and
      in the panel, like the PM counters.

## 5. Dashboard and scenes

- [ ] Metrology scene, if wanted: the rework loop is the honest story
      (`LithoMet`/`DefMet` send a share of lots back three steps).
- [ ] Reticle library in the litho scene once §2 supplies reticles.
- [ ] Results page: starts scale and overlay name on every row; loss-analysis
      columns (setup share, starvation share) per family.
- [ ] `burndown_geom.test.mjs` is data-dependent and flaky; pin its fixture.

## 6. Environment

- [ ] `scripts/dev-up.sh` resolves `npm` to the Windows npm via WSL interop
      when run non-interactively; pin the UI launch to the Linux node
      (`/usr/bin/node node_modules/vite/bin/vite.js`). Same for `FEED_RULE`.
- [ ] Commit the scene work (four scenes, playback, tracking, API history
      and step records, feed PM/wear fields, harness utilization and starts
      scale) — all still uncommitted as of 2026-09-09.
