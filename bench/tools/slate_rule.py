"""
slate_rule -- the CP-SAT slate as a PySCFabSim dispatching rule.

This is the fourth row of the table in docs/adr/0002: fifo / cr / PPO / slate,
one environment, one generator, one horizon, one KPI set. The design and the
measurements behind it are in docs/adr/0009.

  Python (here)                      C++ (dispatch/libfabslate.so)
  ----------------------------       -----------------------------
  routes, due dates, remaining        tool eligibility model
  work, downstream congestion,        CP-SAT assignment, per family
  batch-fill pressure
        |                                     |
        +--> priority, qtime_slack --> plan --+
                  once per cycle, NOT per decision point

THE TUPLE CONTRACT
------------------
greedy.py:71 sorts machine.waiting_lots by lot.ptuple, and greedy.py:83-89
reaches INTO that tuple to form batches: it reads ptuple[0] (the min-run gate)
and splices ptuple[2:] (the priority rule). So the shape is load-bearing.

    slot 0   min-run gate      copied verbatim from the upstream rules
    slot 1   setup time        copied verbatim
    slot 2   -lot.priority     copied verbatim -- hot lots still preempt
    slot 3+  the slate         our contribution

Deviating from slots 0-2 changes batch formation, and the A/B would then be
measuring two things at once.

COVERAGE
--------
A lot that arrives between rebuilds has no token. Those must NOT fall through
to FIFO or a large share of decisions would not be the slate's and the
benchmark would measure a blend. Untokened lots get a solver-consistent score
-- the linearized form of SolverExporter::cost -- so ordering is continuous
across the coverage boundary. `stats()` reports the coverage fraction so a run
that mostly fell back is visible rather than silently reported as "slate".
"""
import sys

from dispatching.dispatcher import Dispatchers  # noqa: E402
from events import LotDoneEvent, MachineDoneEvent  # noqa: E402

import fabslate


# The sentinel that makes the C++ q-time term do nothing:
# qtime_boost = 1 + 600/max(slack, 60) is 1.0000006 at 1e9, and the batch
# rule (should_fire, min_qtime_slack_s <= fixed_process_s * 1.2) can never
# trigger. Still used, but now only for lots that HAVE no live window.
#
# It used to be fed for every lot, correctly: "PySCFabSim parses queue-time
# constraints but does not enforce them (docs/adr/0008). Feeding a real slack
# would have the solver optimise against a signal the environment never
# punishes." That premise died with ADR 0016 -- windows are enforced, a
# violation reworks the lot, and a lot that misses too often is scrapped.
QTIME_INERT = 1e9

# Objective versions (ADR 0017 §12.12). 'v1' is the objective every published
# row was produced with. 'v2' answers the audit's two findings:
#   - the due term is monotone through the cr 1-3 band where waiting lots
#     actually sit (v1 was flat above 2 and reached only 2x at cr = 1);
#   - the cost numerator no longer carries the lot's own process time, which
#     within a family of identical tools only ranked lots shortest-job-first
#     (91% order agreement on the warmed fab). A fixed reference keeps
#     setup relative to *something* without ranking by job length.
OBJECTIVES = ('v1', 'v2')
REF_PROCESS_S = 3600.0


def due_term(cr, objective='v1'):
    """Due-date multiplier on urgency as a function of critical ratio."""
    if objective == 'v2':
        # 1x at cr >= 3, 2x at 1.5, 3x at 1; below 1 the v1 steep term rides
        # on top, continuous at cr = 1.
        u = min(3.0, max(1.0, 3.0 / max(cr, 0.02)))
        if cr < 1.0:
            u *= min(50.0, 1.0 / max(cr, 0.02))
        return u
    u = 1.0 + max(0.0, 2.0 - cr)
    if cr < 1.0:
        u *= min(50.0, 1.0 / max(cr, 0.02))
    return u



def qtime_slack_s(lot, t):
    """Seconds until this lot's open queue-time window lapses, for the C++
    cost term -- or QTIME_INERT when there is nothing live to protect.

    A LAPSED window reports inert, not its (negative) slack, and that is the
    whole subtlety. The C++ term clamps with max(slack, 60.0), so a lot 200
    hours past deadline would come back as the MAXIMUM possible boost and the
    solver would chase the most hopeless work in the fab. That is exactly the
    bug the `qt` sort key shipped with (ADR 0017 §9): invisible on a cold fab,
    and worth 55% of throughput on a warmed one where two thirds of open
    windows are already blown. A blown window cannot be un-blown, so the lot
    is priced as ordinary work.
    """
    w = getattr(lot, 'cqt_waiting', None)
    d = getattr(lot, 'cqt_deadline', None)
    if w is None or d is None:
        return QTIME_INERT
    slack = d - t
    if slack <= 0:
        return QTIME_INERT
    # NORMALISED, not raw seconds. Both C++ q-time terms are hardcoded in
    # MINUTES -- 1 + 600/slack in cost(), 1 + 3600/slack in the CP-SAT
    # objective -- while this fab's windows are 10 to 240 HOURS. Feeding raw
    # slack gave a lot with 16h remaining (the measured p75 of saveable
    # at-risk lots) a boost of 1.010x and 1.063x respectively: numerically
    # switched off, against due-date urgency that reaches 50x. That is why
    # un-inerting the term changed nothing and slate kept cr's divergence
    # signature (adr/0017 §11.1).
    #
    # Passing 600 * (slack / window) makes both formulas window-RELATIVE:
    #     cost()   1 + 1/frac  -> 11x at 10% of the window left, 2x at 100%
    #     CP-SAT   1 + 6/frac  -> 61x at 10%, 7x at 100%
    # so a lot near the end of a 10-hour window and one near the end of a
    # 240-hour window are treated alike, which is what the constraint means.
    window = getattr(lot, 'cqt_window_s', None)
    if not window or window <= 0:
        return slack
    return 600.0 * slack / window


class SlateRule:
    """A ptuple_fcn backed by the C++ planner.

    Pass an instance of this where sim_runner.run() takes a dispatcher.
    Call maybe_rebuild(instance) from before_dispatch so the slate is refreshed
    on the planning cadence rather than per decision.
    """

    # pressure tiers -- the ablation ladder docs/adr/0009 asks for, so each
    # information tier is a row in the results table rather than one
    # undifferentiated "slate" number.
    TIERS = ('none', 'due', 'full', 'flow')
    FALLBACKS = ('score', 'cr', 'qt')

    def __init__(self, instance, solver='cpsat', cycle_s=60.0, budget_s=0.005,
                 pressure='full', threads=1, lazy=True, lib_path=None,
                 fallback='cr', horizon_s=900.0, on_demand=False, objective='v1'):
        if pressure not in self.TIERS:
            raise ValueError(f'pressure must be one of {self.TIERS}')
        if fallback not in self.FALLBACKS:
            raise ValueError(f'fallback must be one of {self.FALLBACKS}')
        if objective not in OBJECTIVES:
            raise ValueError(f'objective must be one of {OBJECTIVES}')
        self.objective = objective
        # On-demand re-solve (docs/NEXT.md §0.5). With the 60 s cycle, one
        # token per tool, carried-over tokens and a 5 ms budget, ~31% of the
        # decisions with a real choice fall to the fallback (adr/0017
        # §12.11.4). When this is on, a tool that frees with two or more
        # lots waiting and NO token for any of them gets its family re-solved
        # right then, from the live queue, before the rule is consulted. The
        # periodic cycle is unchanged; this only fills the gaps between it.
        # Off by default so every existing row is reproducible.
        self.on_demand = bool(on_demand)
        self.demand_solves = 0
        # What scores a lot the slate holds no token for (~half of all
        # decision points at 47% coverage): 'score' is the linearized solver
        # cost, continuous with the plan; 'cr' is critical ratio, the rule
        # that wins under load. Tokened lots still rank ahead of either.
        self.fallback = fallback
        # Look-ahead (docs/adr/0010): lots that will REACH a family within
        # this many seconds -- finishing a step now, or sitting in a route
        # delay -- are planned alongside the lots already waiting, so a tool
        # that frees between rebuilds finds a token for what has arrived
        # since. 0 = plan only the queue. No holds: a tool never waits for a
        # planned lot that is not there yet, it walks its ranking to the
        # first lot that is.
        self.horizon_s = float(horizon_s or 0.0)
        self.instance = instance
        self.cycle_s = float(cycle_s)
        self.budget_s = float(budget_s)
        self.pressure = pressure
        self.threads = int(threads)
        self.lazy = lazy

        self.planner = fabslate.Planner(solver, lib_path=lib_path)

        # tool_id -> lot_idx -> rank, the INVERSE index. The simulator asks
        # "which lot for this machine", never "which tool for this lot", and
        # greedy.py:37 may reassign the machine afterwards -- so a lot->tool
        # map alone cannot be consulted at a decision point.
        self.by_tool = {}
        self.token_of = {}          # lot.idx -> (tool_id, alternate, rank)

        self.last_build_t = None
        self.builds = 0
        self.consults = 0
        self.covered = 0            # consults where the lot had a token
        # Decision-point coverage. The lot-level number above is NOT the
        # interesting one: a machine's waiting_lots can hold hundreds of lots
        # while the slate, which assigns at most one lot per free tool, holds a
        # token for one of them. Lot-level coverage is therefore ~1/queue-depth
        # by construction and says nothing about who decided.
        # What matters is whether the slate had a pick for THIS MACHINE when it
        # was asked -- that is the fraction of decisions the slate actually
        # made, and the number that says whether the benchmark measures slate
        # or measures its fallback.
        self.decisions = 0
        self.decisions_covered = 0
        # Coverage is only meaningful over decisions where there was a choice.
        # A tool with ONE eligible waiting lot dispatches it under any rule --
        # solver token or not -- so counting it as "fallback" understates the
        # solver and counting it as "covered" overstates it. Split them out:
        self.decisions_forced = 0            # exactly one eligible lot waiting
        self.decisions_choice = 0            # two or more eligible lots waiting
        self.decisions_choice_covered = 0    # ...and the solver had a token
        # Candidate-set size per decision, split by who decided. Buckets are
        # eligible waiting lots: '1', '2', '3-5', '6+'. And because SLATE is a
        # lot-TOOL assignment, a single waiting lot is still an assignment
        # opportunity when other tools in its family are idle (which tool
        # takes it matters for setup state); '1+idle' counts those.
        self.cand_hist = {k: [0, 0] for k in ('1', '1+idle', '2', '3-5', '6+')}  # [fallback, covered]
        self._cur_machine = None
        self.solve_time_s = 0.0
        self.last_stats = {}
        self._family_wip = {}
        self._dirty = set()
        self._prev_sig = {}
        self._tool_state = {}

        self._register_setups()
        self._register_tools()

    # -- registration -------------------------------------------------------
    def _register_setups(self):
        # instance.setups maps (from, to) -> seconds and is ASYMMETRIC. It is
        # passed through unchanged; collapsing it would mis-order changeovers.
        pairs = [(f, t, s) for (f, t), s in self.instance.setups.items()]
        self.planner.set_setup_matrix(pairs, default_s=0.0)

    def _register_tools(self):
        self._machines = list(self.instance.machines)
        # Qualification is read from the SAME overlay object the instance is
        # bound to (adr/0013 §2), not from a second parse of the table. If the
        # solver and the simulator could disagree about the matrix, `slate`
        # would plan tokens the simulator refuses to serve and the row would
        # be measuring the fallback again -- the summary §4.4 failure. One
        # object, two readers, is the same guarantee the dataset symlink gives.
        self._ordinal = {}
        for fam, machines in self.instance.family_machines.items():
            for ordinal, m in enumerate(machines):
                self._ordinal[m.idx] = (fam, ordinal)
        # Same single-object rule for the mask library (adr/0014 §3.4): the
        # solver reads the reticle ids off the object the simulator enforces,
        # so the two cannot disagree about which lot needs which mask.
        self._reticles = getattr(self.instance, 'reticles', None)
        self.planner.set_tools([self._tool_dict(m) for m in self._machines])
        ov = getattr(self.instance, 'overlay', None)
        if ov is not None:
            n = sum(1 for m in self._machines if self._qualified_parts(m))
            print(f'  overlay {ov.name} ({ov.hash}): {n} of '
                  f'{len(self._machines)} tools carry a qualified-part list',
                  flush=True)
        if self._reticles is not None:
            ns = sum(1 for m in self._machines
                     if m.family in self._reticles.scanner_families)
            print(f'  reticles ({self._reticles.hash}): '
                  f'{len(set(r for r, _ in self._reticles.table.values()))} '
                  f'masks over {ns} scanners, '
                  f'transport {self._reticles.transport_s:g}s', flush=True)

    def _qualified_parts(self, m):
        """The parts tool `m` may run, or () for "every part".

        Empty is the pristine convention all the way down: an absent pair in
        the overlay table, an empty `qualified_parts` on the wire, and an
        empty recipe list on FamilyTool all mean unconstrained.
        """
        ov = getattr(self.instance, 'overlay', None)
        if ov is None:
            return ()
        fam, ordinal = self._ordinal.get(m.idx, (None, None))
        if fam is None:
            return ()
        return tuple(ov.parts_for(fam, ordinal))

    def _tool_dict(self, m):
        # Every machine is planned for, not just the ones free at this instant.
        #
        # instance.usable_machines is the set awaiting a decision RIGHT NOW --
        # a handful out of 1,313. Planning only for those was the first thing
        # tried and it caps the slate at a few tokens per cycle, so ~94% of
        # decisions fell through to the fallback and the run measured the
        # fallback rather than the slate.
        #
        # Planning across the whole fab is also what the production dispatcher
        # does: build a slate, then serve lookups as tools free. A token for a
        # machine that is busy or down is simply never consulted -- it costs
        # one variable and nothing else -- while a machine that frees between
        # rebuilds now finds a pick waiting. The staleness that introduces is
        # the quantity docs/adr/0002 wants measured, not a defect to design out.
        return {
            'tool_id': str(m.idx),
            'family': m.family,
            'current_setup': m.current_setup or '',
            'capacity': 1,
            'online': True,
            'speed': getattr(m, 'speed', 1.0) or 1.0,
            'min_run_length': 0,
            'min_runs_left': int(m.min_runs_left or 0)
                             if m.min_runs_left is not None else 0,
            'min_runs_setup': m.min_runs_setup or '',
            'qualified_parts': self._qualified_parts(m),
            'is_scanner': (self._reticles is not None
                           and m.family in self._reticles.scanner_families),
        }

    # -- the planning cycle -------------------------------------------------
    def maybe_rebuild(self, instance=None):
        inst = instance or self.instance
        t = inst.current_time
        if self.last_build_t is None or (t - self.last_build_t) >= self.cycle_s:
            self.rebuild(inst)
            return True
        if self.on_demand:
            # The machine sim_runner is about to ask for is the first element
            # of usable_machines (greedy.get_lots_to_dispatch_by_machine takes
            # the same `for m in set: break`), so peek the same way. Only a
            # decision with a real choice and no pick is worth a solve: one
            # lot waiting is forced under any rule.
            m = None
            for m in inst.usable_machines:
                break
            if m is not None and len(m.waiting_lots) >= 2:
                held = self.by_tool.get(str(m.idx))
                if not (held and any(l.idx in held for l in m.waiting_lots)):
                    self.rebuild(inst, only={m.family})
                    self.demand_solves += 1
                    return True
        return False

    def rebuild(self, instance=None, only=None):
        """Rebuild the slate; `only` restricts it to a set of families.

        With `only`, this is the on-demand path: the queue of those families
        is re-solved from live state, their tool state is synced, nothing
        else is touched (no look-ahead scan, no family-WIP refresh, no token
        pruning outside the set, no reset of the periodic clock).
        """
        inst = instance or self.instance
        t = inst.current_time

        if only:
            lots = [l for l in self._ready_lots(inst) if l.actual_step.family in only]
            upcoming = []
            self._sync_tools(t, families=only)
            dirty = set(only)
            solve_lots = [(l, l.actual_step, 0.0) for l in lots]
            if not solve_lots:
                return
            payload = [self._lot_dict(l, t, st, arr) for l, st, arr in solve_lots]
            tokens, stats = self.planner.plan(
                payload, budget_s=self.budget_s, threads=self.threads)
            for lot, _st, _arr in solve_lots:
                self.token_of.pop(lot.idx, None)
            for lot_index, tool_id, alternate, rank, _exp in tokens:
                lot = solve_lots[lot_index][0]
                self.token_of[lot.idx] = (tool_id, alternate, rank)
            self.by_tool = {}
            for lot_idx, (tool_id, _alt, rank) in self.token_of.items():
                self.by_tool.setdefault(tool_id, {})[lot_idx] = rank
            self.solve_time_s += stats['solve_time_s']
            return

        lots = self._ready_lots(inst)
        self._family_wip = _family_counts(lots)
        upcoming = self._upcoming_lots(inst, t)      # [(lot, step, arrival_s)]
        up_fams = {st.family for _, st, _ in upcoming}
        # Families whose TOOL state moved are dirty too, not just those whose
        # queue moved. A machine that changed setup re-prices every changeover
        # in its family, so a slate built before it is stale even though the
        # waiting lots are identical.
        tool_dirty = self._sync_tools(t)

        # Only families whose composition moved are re-solved, and -- the part
        # that matters for wall clock -- only THEIR lots are marshalled. The
        # first cut sent all ~2,500 waiting lots across the boundary every
        # cycle and rebuilt 1,313 tool structs with them; at ~1,440 cycles per
        # simulated day that marshalling, not the solve, was the run's cost.
        # Carry-over lives here rather than in C++ for the same reason: tokens
        # for a quiet family are already in self.token_of, so re-sending its
        # lots just to have them handed back is pure overhead.
        dirty = self._dirty_families(lots) if self.lazy else None
        if dirty is not None:
            dirty |= tool_dirty
            dirty |= up_fams          # an arrival within the horizon re-prices its family
        if dirty is None:
            solve_lots = [(l, l.actual_step, 0.0) for l in lots] + upcoming
        elif dirty:
            solve_lots = [(l, l.actual_step, 0.0) for l in lots if l.actual_step.family in dirty] \
                + [u for u in upcoming if u[1].family in dirty]
        else:
            solve_lots = []

        stats = {'assigned': 0, 'ready': len(lots), 'variables': 0,
                 'solve_time_s': 0.0, 'objective': 0.0, 'status': 'skipped',
                 'detail': 'no dirty family'}
        if solve_lots:
            payload = [self._lot_dict(l, t, st, arr) for l, st, arr in solve_lots]
            tokens, stats = self.planner.plan(
                payload, budget_s=self.budget_s, threads=self.threads)

            if dirty is None:
                self.token_of = {}
            else:
                # Drop stale tokens for the families being re-solved; every
                # other family keeps what it had.
                for lot, _st, _arr in solve_lots:
                    self.token_of.pop(lot.idx, None)
            for lot_index, tool_id, alternate, rank, _exp in tokens:
                lot = solve_lots[lot_index][0]
                self.token_of[lot.idx] = (tool_id, alternate, rank)

        # A lot that has left the ready pool must not keep a token, or a stale
        # entry would answer for a lot that is already running.
        live = {l.idx for l in lots} | {l.idx for l, _st, _arr in upcoming}
        if len(self.token_of) > len(live):
            self.token_of = {k: v for k, v in self.token_of.items() if k in live}

        self.by_tool = {}
        for lot_idx, (tool_id, _alt, rank) in self.token_of.items():
            self.by_tool.setdefault(tool_id, {})[lot_idx] = rank

        self.last_build_t = t
        self.builds += 1
        self.solve_time_s += stats['solve_time_s']
        self.last_stats = stats

    def _sync_tools(self, t=None, families=None):
        """Push only the tool state that moved; return the families it touched.

        `families` restricts the scan to those families' machines (the
        on-demand path, where scanning all 1,313 per decision would cost
        more than the solve).

        With a look-ahead horizon a tool counts as available only if it is
        free now or frees within the horizon (its MachineDoneEvent is known):
        tokens then go to tools that will actually ask, instead of one lot
        being reserved for every tool in the family, hours-busy ones
        included. Without a horizon every tool is available, as before.
        """
        changed = 0
        touched = set()
        scan = enumerate(self._machines)
        if families is not None:
            scan = [(i, m) for i, m in enumerate(self._machines) if m.family in families]
        for i, m in scan:
            avail = True
            if self.horizon_s > 0 and t is not None and m.events:
                done = [ev.timestamp for ev in m.events if isinstance(ev, MachineDoneEvent)]
                if done and min(done) - t > self.horizon_s:
                    avail = False
            cur = (m.current_setup or '',
                   int(m.min_runs_left or 0) if m.min_runs_left is not None else 0,
                   m.min_runs_setup or '', avail)
            if self._tool_state.get(i) == cur:
                continue
            self._tool_state[i] = cur
            self.planner.set_tool_state(
                i, current_setup=cur[0], min_runs_left=cur[1],
                min_runs_setup=cur[2], online=cur[3])
            touched.add(m.family)
            changed += 1
        if changed:
            self.planner.flush_tools()
        return touched

    def _ready_lots(self, inst):
        """Every lot waiting somewhere, deduplicated.

        A waiting lot appears in waiting_lots of every machine in its family,
        so iterating machines double-counts; the ready POOL is the set.
        """
        seen = {}
        for m in inst.machines:
            for lot in m.waiting_lots:
                if lot.actual_step is not None:
                    seen[lot.idx] = lot
        return list(seen.values())

    def _upcoming_lots(self, inst, t):
        """Lots not yet waiting that a family will see within the horizon.

        Every lot on a tool is a LotDoneEvent with a known time, so its arrival
        at the next family is known; a route delay step (a timed hold, ADR
        0008) is seen through, so a lot finishing metrology and sitting in a
        2 h hold is planned for the family after it. Returns (lot, step,
        seconds until arrival).
        """
        if self.horizon_s <= 0:
            return []
        out = []
        limit = t + self.horizon_s
        for ev in inst.events.arr:
            if ev.timestamp > limit:
                break
            if not isinstance(ev, LotDoneEvent):
                continue
            for lot in ev.lots:
                steps = lot.remaining_steps
                if not steps:
                    continue
                arrive, k = ev.timestamp, 0
                nxt = steps[0]
                while nxt is not None and str(nxt.family).startswith('Delay'):
                    arrive += nxt.processing_time.avg()
                    k += 1
                    nxt = steps[k] if k < len(steps) else None
                if nxt is None or arrive > limit:
                    continue
                out.append((lot, nxt, max(0.0, arrive - t)))
        return out

    def _dirty_families(self, lots):
        """Families whose queue moved since the last cycle.

        Signature is the family's waiting-lot multiset, hashed cheaply as
        (count, sum of lot ids). Two different queues can collide in principle;
        in practice a lot entering or leaving changes both terms, and the cost
        of a rare missed rebuild is one stale cycle, not a wrong answer.

        Most families are quiet in any given 60s window, and re-solving only
        the ones that moved is what makes ~1M cycles tractable (adr/0009).
        Returns None on the first build, meaning "solve everything".
        """
        sig = {}
        for l in lots:
            f = l.actual_step.family
            c, s = sig.get(f, (0, 0))
            sig[f] = (c + 1, s + l.idx)

        if not self._prev_sig:
            self._prev_sig = sig
            return None
        dirty = {f for f, v in sig.items() if self._prev_sig.get(f) != v}
        # A family that emptied out is gone from sig but its tokens are stale.
        dirty |= {f for f in self._prev_sig if f not in sig}
        self._prev_sig = sig
        return dirty

    # -- the pressure layer -------------------------------------------------
    def _lot_dict(self, lot, t, step=None, arrival_s=0.0):
        step = step or lot.actual_step
        # A lot still on its way is worth less to this family than one on the
        # shelf, by how far away it is: five minutes out halves its claim.
        u = self._urgency(lot, t, step) / (1.0 + arrival_s / 300.0)
        return {
            'lot_id': str(lot.idx),
            'family': step.family,
            'setup_group': step.setup_needed or '',
            'step': step.step_name,           # batch key, part 1
            'part': lot.part_name,            # batch key, part 2
            'batch_min': int(step.batch_min or 1),
            'batch_max': int(step.batch_max or 1),
            'wafers': int(lot.pieces or 25),
            'priority': u,
            'qtime_slack_s': qtime_slack_s(lot, t),
            # The mask this lot needs at THIS step (adr/0014). '' whenever
            # there is no library or the step is not a scanner step, which is
            # the same "empty is unconstrained" convention the qualified-part
            # list uses. solver.hpp groups the scanner assignments by it and
            # forbids two of them at once.
            'reticle': (self._reticles.lot_reticle(lot)
                        if self._reticles is not None else ''),
            'step_process_s': (REF_PROCESS_S if self.objective == 'v2'
                               else step.processing_time.avg()),
            'due_s': lot.deadline_at,
            'waiting_s': max(0.0, t - (lot.free_since or t)),
        }

    def _urgency(self, lot, t, step=None):
        """Collapse everything Python knows into the scalar C++ consumes.

        fabdisp's Lot::priority is documented as coming "from the tactical
        urgency vector" -- it always assumed urgency was computed upstream.
        This is that computation. Higher means more urgent; the C++ cost
        function divides by it.
        """
        u = max(float(lot.priority), 0.01)
        if self.pressure == 'none':
            return u

        # Tier 1 -- due-date pressure. cr < 1 means the lot cannot make its due
        # date even with zero further queueing. Above 1 the curve is gentle
        # and flat past 2 where a lot has slack to spare; below 1 it follows
        # the critical ratio's own slope, uncapped (bounded only numerically):
        # a lot twice as late is twice as urgent, which is the ordering that
        # held on-time under load where the old x3 cap did not (ADR 0012 s3).
        cr = lot.cr(t)
        u *= due_term(cr, self.objective)

        # Ageing, so a lot cannot be starved indefinitely by a stream of more
        # urgent work. Deliberately weak: one week of queueing doubles it.
        u *= 1.0 + min(1.0, max(0.0, t - (lot.free_since or t)) / 604800.0)

        if self.pressure not in ('full', 'flow'):
            return u
        flow = self.pressure == 'flow'

        # Tier 2 -- downstream congestion. None of fifo/cr/lifo look past the
        # current step. Pulling a lot into an already-congested next family
        # just moves the queue; feeding a starving one keeps a bottleneck fed.
        rem = lot.remaining_steps
        if step is None or step is lot.actual_step:
            nxt = rem[0] if rem else None
        else:
            # planned ahead: the step after `step` in the lot's remaining route
            try:
                k = rem.index(step)
                nxt = rem[k + 1] if k + 1 < len(rem) else None
            except ValueError:
                nxt = None
        if nxt is not None:
            ahead = self._family_wip.get(nxt.family, 0)
            capacity = max(1, len(self.instance.family_machines.get(nxt.family, ())))
            load = ahead / capacity
            # load 0 (starving downstream) -> 1.25x, load >= 5 -> 0.8x.
            # 'flow' leans harder on it: 1.5x for a starving next family,
            # 0.7x for a flooded one -- keep bottlenecks fed, stop pushing
            # into queues that only move the wait.
            if flow:
                u *= max(0.7, 1.5 - 0.16 * min(load, 5.0))
            else:
                u *= max(0.8, 1.25 - 0.09 * min(load, 5.0))

        # Batch formation: a lot whose step batches and whose cohort is already
        # near the minimum is worth more, because dispatching it lets a furnace
        # fire instead of sitting half full. greedy.py already maximises batch
        # size within a tie; this makes the batch visible BEFORE the tie.
        step = step or lot.actual_step
        if step.batch_max and step.batch_max > 1:
            cohort = self._family_wip.get(step.family, 0)
            if cohort >= (step.batch_min or 1):
                u *= 1.3 if flow else 1.1
        return u

    # -- the decision point -------------------------------------------------
    def __call__(self, lot, time, machine=None, setups=None):
        """ptuple_fcn. Called ~16M times in a 730-day run; keep it cheap."""
        if machine is None:
            # The no-machine form is used for lot-centric ordering. Mirror the
            # upstream rules' shape.
            return (-lot.priority, self._score(lot, time, None, None))

        self.consults += 1
        step = lot.actual_step

        # greedy.py scores every waiting lot for one machine before sorting, so
        # a change of machine marks a new decision point.
        if machine.idx != self._cur_machine:
            self._cur_machine = machine.idx
            self.decisions += 1
            held = self.by_tool.get(str(machine.idx))
            covered = bool(held) and any(l.idx in held for l in machine.waiting_lots)
            if covered:
                self.decisions_covered += 1
            n = len(machine.waiting_lots)
            if n <= 1:
                self.decisions_forced += 1
                inst = self.instance
                fam = getattr(machine, 'family', None)
                idle = sum(1 for m in inst.family_machines.get(fam, ())
                           if inst.free_machines[m.idx]) if fam is not None else 1
                b = '1+idle' if idle >= 2 else '1'
            else:
                self.decisions_choice += 1
                if covered:
                    self.decisions_choice_covered += 1
                b = '2' if n == 2 else ('3-5' if n <= 5 else '6+')
            self.cand_hist[b][1 if covered else 0] += 1
            self._fallback_src = f'rule:slate-fallback-{self.fallback}' if self.fallback != 'score' else 'rule:slate-fallback'

            # Stamp WHO decided, using the protocol sim_feed already defines:
            #
            #   src = instance.dispatch_source or f'rule:{rule}'
            #   optimized = not src.startswith('rule:')
            #
            # so a source outside the 'rule:' namespace counts as an optimised
            # decision and feeds optimized_pct on the dashboard. Without this
            # the feed falls back to 'rule:slate' and scores every slate
            # decision as unoptimised -- the metric would read 0% for the one
            # rule it exists to measure.
            #
            # The distinction is per DECISION POINT, not per lot: at this
            # moment we know whether the slate holds a pick for this machine,
            # which is exactly what coverage counts. A decision with no pick
            # is served by the fallback score, so it is stamped back into the
            # 'rule:' namespace and is honestly NOT optimised.
            self.instance.dispatch_source = (
                'slate' if covered else self._fallback_src)

        # Slots 0 and 1: verbatim from the upstream rules. See the tuple
        # contract at the top of this module.
        gate = 0 if (machine.min_runs_left is None or
                     machine.min_runs_setup == step.setup_needed) else 1
        setup = Dispatchers.get_setup(step.setup_needed, machine,
                                      step.setup_time, setups)

        tok = self.token_of.get(lot.idx)
        if tok is not None:
            tool_id, alternate, rank = tok
            mid = str(machine.idx)
            if tool_id == mid:
                tier = 0        # the slate picked this lot for this tool
            elif alternate == mid:
                tier = 1        # failover target, no re-solve needed
            else:
                tier = 2        # planned elsewhere; run it only if nothing better
            self.covered += 1
            lot.ptuple = (gate, setup, -lot.priority, tier, rank)
        else:
            # No token: score with the linearized C++ cost so ordering is
            # continuous across the coverage boundary rather than snapping
            # to FIFO.
            # Coverage is ~50%, so the fallback decides about half of a
            # slate run and a row with a weak one measures the fallback
            # (bench/README.md). At the ADR 0017 operating point `cr` is not
            # merely weaker than `qt`, it DIVERGES -- WIP 2199 -> 3822 at
            # 15.2% on-time against qt's stationary 81.7% -- so a cr-fallback
            # row would lose on the uncovered half whatever the solver did
            # with the covered one.
            if self.fallback == 'cr':
                rank = lot.cr(time)
            elif self.fallback == 'qt':
                # the `qt` tuple's tier, so the uncovered half is ordered by
                # the rule slate has to beat rather than by the one it beats.
                sl = qtime_slack_s(lot, time)
                rank = (0, sl, lot.cr(time)) if sl < QTIME_INERT \
                    else (1, 0.0, lot.cr(time))
            else:
                rank = self._score(lot, time, machine, setup)
            lot.ptuple = (gate, setup, -lot.priority, 3, rank)
        return lot.ptuple

    def _score(self, lot, time, machine, setup):
        """The linearized form of SolverExporter::cost. Lower is better."""
        step = lot.actual_step
        proc = REF_PROCESS_S if self.objective == 'v2' else step.processing_time.avg()
        time_cost = (setup or 0.0) + proc
        return time_cost / max(self._urgency(lot, time), 0.01)

    # -- who decided --------------------------------------------------------
    # Slot 3 of the ptuple already carries the tier, so the reason for a
    # dispatch can be read back off the lot that won without any extra
    # bookkeeping during scoring -- which matters, because scoring runs ~16M
    # times per 730-day run and the winner is not known until after the sort.
    REASONS = {
        0: 'slate',            # the slate picked this lot for this tool
        1: 'slate-alt',        # this tool was the failover target
        2: 'slate-elsewhere',  # planned for another tool, run here anyway
        3: 'fallback',         # no token: solver-consistent score decided
    }

    def reason_for(self, lot):
        """Why this lot was the one dispatched. '' if it was never scored."""
        t = getattr(lot, 'ptuple', None)
        if not t or len(t) < 4:
            return ''
        return self.REASONS.get(t[3], '')

    # -- reporting ----------------------------------------------------------
    def stats(self):
        # `coverage` is the decision-level number: the share of decision points
        # the slate actually decided. `lot_token_share` is the lot-level one,
        # kept only because it is cheap and diagnostic -- it is bounded by
        # queue depth and should not be read as quality.
        cov = (self.decisions_covered / self.decisions) if self.decisions else 0.0
        share = (self.covered / self.consults) if self.consults else 0.0
        return {
            'solver': self.planner.solver,
            'solver_available': self.planner.solver_available,
            'pressure': self.pressure,
            'objective': self.objective,
            'cycle_s': self.cycle_s,
            'builds': self.builds,
            'on_demand': self.on_demand,
            'demand_solves': self.demand_solves,
            'decisions': self.decisions,
            'coverage': round(cov, 4),
            # the split that makes coverage meaningful (adr/0017): decisions
            # with one eligible lot are forced under ANY rule; effective
            # coverage is solver decisions among those with a genuine choice
            'decisions_forced': self.decisions_forced,
            'decisions_choice': self.decisions_choice,
            'decisions_choice_covered': self.decisions_choice_covered,
            'candidate_hist': {k: {'fallback': v[0], 'covered': v[1]}
                               for k, v in self.cand_hist.items()},
            'effective_coverage': round(
                self.decisions_choice_covered / self.decisions_choice, 4)
                if self.decisions_choice else None,
            'consults': self.consults,
            'lot_token_share': round(share, 4),
            'plan_time_s': round(self.solve_time_s, 3),
            'last': self.last_stats,
        }

    def banner(self):
        s = self.stats()
        warn = '' if s['solver_available'] else \
            '  !! OR-Tools NOT linked -- these are greedy numbers wearing cpsat\'s name'
        return (f"  slate: solver={s['solver']} pressure={s['pressure']} "
                f"cycle={s['cycle_s']:.0f}s{warn}")


def _family_counts(lots):
    c = {}
    for l in lots:
        f = l.actual_step.family
        c[f] = c.get(f, 0) + 1
    return c


# ---------------------------------------------------------------------------
# v0 validation: a rule that reproduces `cr` exactly.
#
# Before any solver is trusted, the harness itself has to be shown not to
# change the answer. This routes through the same call path slate_rule uses --
# same signature, same tuple shape -- but returns CR's ordering. A run with
# this must reproduce `--dispatcher cr` to the digit. If it does not, the
# plumbing is wrong and every downstream number is too.
# ---------------------------------------------------------------------------
class CrPassthrough:
    def __init__(self):
        self.consults = 0

    def __call__(self, lot, time, machine=None, setups=None):
        self.consults += 1
        return Dispatchers.cr_ptuple_for_lot(lot, time, machine, setups)

    def maybe_rebuild(self, instance=None):
        return False

    def stats(self):
        return {'solver': 'none (cr passthrough)', 'consults': self.consults}

    def banner(self):
        return '  slate: CR PASSTHROUGH -- harness validation, not a real rule'
