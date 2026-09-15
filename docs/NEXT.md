# Next steps

One list, ordered by what gates what. The C++ placeholders stay in
[`dispatch/README.md`](../dispatch/README.md#placeholders-by-priority) and the
benchmark's open items in [`bench/README.md`](../bench/README.md#still-open);
this page is the programme they hang off. Dates are when an item was written.

## 0. After the replicate batch (opened 2026-09-15, gates everything below)

The replicate batch (ADR 0017 §12.11) settled the order of the next work:
the solver wins on the hard seed on every replicate and loses on the easy
seeds on every replicate, so the two things to fix first are the constraint
itself and the operating point it was calibrated at. In order, each gating
the next:

1. [x] **Fix the queue-time window open** (ADR 0016 §8) — *code done 2026-09-15 on `cqt-window-fix`; nothing re-run yet.* It opens at the
      *start* of the entrance step in `instance.dispatch()`; SMT2020 opens it
      at *completion*. Move the open to `free_up_lots()`. At native scale the
      entrance step eats a median 36% of the window, so this is not tidying:
      the scale-10 operating point was chosen partly because of it. **Every
      result depends on the definition; everything reruns after this.**
2. [ ] **Bring the scale back toward 1.** On the corrected window, sweep
      queue-time scale downward (10 → 1) and release rate on all five seeds
      with the sort keys only — cheap and bit-deterministic. Find the lowest
      scale at which the fab is viable under `qt`; the goal is the dataset's
      own windows, not a multiplier.
3. [ ] **Recalibrate the operating point across seeds, not on seed 0.** At
      the current point the tuned rule is at 97–99.7% on seeds 1–3, so
      there is nothing to win there. **Pre-register** a stress criterion on
      a policy-independent quantity before any solver run (e.g. tuned `qt`
      below 95% on-time on every seed) and pick the lowest load that meets
      it. Do not drop seeds after seeing solver results; make the operating
      point hard on all of them.
4. [ ] **Solver replicates at that point on all five seeds**, including seed
      4, the second hard draw that has no solver runs today.
5. [~] **Coverage toward 100% where the solver is eligible** — *`--slate-on-demand` built 2026-09-15; 3-day cold smoke: effective coverage 69 → 78 %, raw 50 → 56 %, 12.6k demand solves, wall +28 %. Not yet run at length.* A decision is
      the solver's only when the freed tool holds a token for a lot in its
      queue; the 60 s cycle, one token per tool, carried-over tokens and a
      5 ms budget leave ~31% of real choices to the fallback (0017 §12.11.4).
      Candidates, cleanest first: re-solve a family on demand when a tool
      frees without a token; a ranked token list per tool instead of one;
      a shorter cycle. All cost wall clock; measure staleness as ADR 0002
      asks rather than assume it.
6. [~] **Lot transport time** — *`--transport-s` built 2026-09-15; 3-day cold
      smoke at 300 s: throughput −4 %, cycle time +0.4 d, as it should. Not yet
      run at length.* Correction to the first draft of this item: reticle moves are
      NOT free — the mask library (ADR 0014) already charges `transport_s`
      (900 s by default) on every scanner-to-scanner move, landed in the
      setup. What is free is the **lot**: PySCFabSim carries a per-step
      `transport_time` that the dataset never fills, and `get_times` already
      adds it after each step. `--transport-s` fills it with one constant on
      every family-to-family move (never into a Delay hold), keyed into the
      checkpoint name (`_tr300`). SMT2020 ships no transport data, so it is
      a declared modelling parameter to sweep (minutes, not hours). Why it
      matters for the solver question: with lot moves costing time, a lot is
      not at its next family the instant it finishes, so "which tool, now"
      and "which tool, in five minutes" stop being the same decision, and
      the look-ahead horizon (ADR 0010) the planner already carries becomes
      load-bearing. The reticle question stays as §2 states it: the masks
      have to be *scarce* (copies, volume) before transport between scanners
      can bind. Sequence after 1–4.
7. [ ] **Objective audit and the imitation floor** (§3b). The easy-seed loss
      has a signature — shorter cycle time, thin lateness on every product —
      that says the objective prefers short jobs to thin-margin dates. Check
      the urgency curve just above CR = 1 first; then fit the objective to
      reproduce `qt` on an easy seed before learning anything.

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
- [x] **Reticles** per (part, litho layer) — **built, ADR 0014.** Exclusive
      across scanners, transport between them, copies per layer. The simulator
      has the resource, `slate_rule` sends the reticle id and the scanner set,
      and the C++ planner's reticle fields now bind (both paths have carried
      `AddAtMostOne` over scanners sharing a mask since 0009; nothing had ever
      fed them an id). Gates held: pristine fp `8d77d45c4c2654a3` unchanged,
      every 0013 overlay hashes as before, 97/97 C++ tests pass.
- [ ] **Make the masks contend, which is the open question** (0014 §6). The
      coupling argument is necessary and not sufficient: with transport zeroed
      the library is indistinguishable from pristine on LVHM at 1.00× (493
      lots against 495), because 251 masks over 82 scanners sit at ~27% of a
      mask-day and a blocked lot never idles a scanner. A mask has to be
      scarce enough that blocking propagates. For a high-mix fab the lever is
      **volume, not mix**: `--starts-part part_1=3.3` takes one part's masks
      to ~89% while the rest scale down to hold total starts at 57 lots/day.
      Warm under the mix (the checkpoint key carries it) and read `by_part` —
      a fab-wide average cannot register a mask on a high-mix fab.
- [ ] **Queue-time enforcement** (CQT columns): scrap or rework on violation;
      un-inert the planner's q-time term (`QTIME_INERT` in `slate_rule.py`).
      Named in 0014 §5 as the successor if masks do not separate the rules,
      on the same "built and inert" grounds — and because it is the only
      candidate that gives the fab a way to **lose work**. Today a bad
      decision can only make a lot late, and tardiness below the knee is a
      few lot-days across 1,300 tools.
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

## 3b. Learn the objective online (opened 2026-09-14)

The one unchecked constant that inverted ADR 0017's verdict is the argument
for this: the solver's objective was hand-guessed, and a fab whose mix and
tool set evolve will make any one-off fit stale the same way.

- **Signals at the 60-second cadence**, not the 180-day one: whether a
  promoted lot made its queue-time window; which families the `qt` fallback
  handled as well as the solver; and a learned state value `V(state)` so a
  per-solve decision carries its longer-horizon cost (the ADP route; the
  vendored PPO scaffold is the seed).
- **The learner evolves; the gate never does.** Conservation, trailing WIP
  slope and scrap rate run continuously; parameters may move only while
  they hold, with bounded step sizes and rollback to the last admissible
  set. The short-window trap (0017 §11.2) is exactly what this guards.
- **Prerequisite:** a deterministic CP-SAT budget, so any parameter move
  can be replayed and audited.
- **Freeze only for benchmarks.** A comparison needs a fixed object on each
  side; production does not.

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

## 5a. Layout-derived transport (new; the positioning half is done)

Tools now have a position: each family takes a contiguous run of its zone's
cells and each tool a slot within one (`Floorplan.reassign`, `cell_template`
in `config/floorplan.json`). Nothing outside the API reads it yet, which is
the point of the rest of this section — ADR 0008 §2 is the standing account
of what is missing.

- [ ] **Cell-level distance.** Build a distance matrix from `x_m`/`y_m` plus
      the `track` topology already in `floorplan.json` (`interbay_segs`,
      `intrabay` — authored, currently read by nothing) and emit a real
      multi-row `fromto.txt`. The mechanism is already live and degenerate:
      `file_instance.py:28-45` keys transport on `(FROMLOC, TOLOC)` and every
      family's `STNFAMLOC` is `Fab`, so all 22,000 moves a day draw the same
      U(5, 10) min.
- [ ] **Decompose, do not replace.** Manhattan track distance over the 72
      non-stocker cells is mean 108 m, max 217 m — 0.45 min at 4 m/s against
      a 7.5 min draw, ~6%. So `t = handling + distance/speed + contention`,
      calibrated so the mean still lands near 7.5 min **at slate @ 1.03×
      starts** (ADR 0012's operating point). Swapping in distance ÷ speed
      alone cuts transport ~94% and every cycle-time KPI "improves": an
      artifact. The tail is deliberately unconstrained — congestion's whole
      signature is excursions above the mean, and U(5, 10) has none.
- [ ] **Per-candidate travel in the dispatcher.** `instance.get_times()`
      charges `remaining_steps[0].transport_time` *after* the decision and
      keys it on the route step pair, so no policy can prefer a nearer tool.
      Location has to move from family to machine (the slots above) and the
      cost has to be computable per candidate inside the ranking loop.
      `DE_FE_86` has 118 machines over four cells: a family-keyed matrix
      cannot express two of them being 40 m apart.
- [ ] **Transport as a contended resource.** Finite vehicles, moves that
      queue. Note transport time then becomes *endogenous* — it depends on
      the policy — so any calibration must name both policy and starts scale.
- [ ] **Bug, independent of the above**: `generator_instance.py:75` reads
      `{('Fab', 'Fav'): ...}` — `Fav`. The key never matches, so generated
      (non-file) instances get **zero** transport time, not 5 min.
- [ ] Revisit cell capacity if the above lands: slotting at a real 4.5 m
      footprint overruns the synthetic cells (ETC 6.9×, CLN and LIT 4.6×).
      Reported on `/api/layout` as `capacity` rather than hidden behind a
      shrunken pitch, but a transport model that takes metres seriously will
      want a grid that is physically credible.

## 6. Environment

- [ ] `scripts/dev-up.sh` resolves `npm` to the Windows npm via WSL interop
      when run non-interactively; pin the UI launch to the Linux node
      (`/usr/bin/node node_modules/vite/bin/vite.js`). Same for `FEED_RULE`.
- [ ] Commit the scene work (four scenes, playback, tracking, API history
      and step records, feed PM/wear fields, harness utilization and starts
      scale) — all still uncommitted as of 2026-09-09.
