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

import fabslate


# PySCFabSim parses queue-time constraints but does not enforce them
# (docs/adr/0008). Feeding a real slack would have the solver optimise against
# a signal the environment never punishes, so the q-time term in the C++ cost
# function is deliberately held inert.
QTIME_INERT = 1e9


class SlateRule:
    """A ptuple_fcn backed by the C++ planner.

    Pass an instance of this where sim_runner.run() takes a dispatcher.
    Call maybe_rebuild(instance) from before_dispatch so the slate is refreshed
    on the planning cadence rather than per decision.
    """

    # pressure tiers -- the ablation ladder docs/adr/0009 asks for, so each
    # information tier is a row in the results table rather than one
    # undifferentiated "slate" number.
    TIERS = ('none', 'due', 'full')

    def __init__(self, instance, solver='cpsat', cycle_s=60.0, budget_s=0.005,
                 pressure='full', threads=1, lazy=True, lib_path=None):
        if pressure not in self.TIERS:
            raise ValueError(f'pressure must be one of {self.TIERS}')
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
        self.planner.set_tools([self._tool_dict(m) for m in self._machines])

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
        }

    # -- the planning cycle -------------------------------------------------
    def maybe_rebuild(self, instance=None):
        inst = instance or self.instance
        t = inst.current_time
        if self.last_build_t is not None and (t - self.last_build_t) < self.cycle_s:
            return False
        self.rebuild(inst)
        return True

    def rebuild(self, instance=None):
        inst = instance or self.instance
        t = inst.current_time

        lots = self._ready_lots(inst)
        self._family_wip = _family_counts(lots)
        # Families whose TOOL state moved are dirty too, not just those whose
        # queue moved. A machine that changed setup re-prices every changeover
        # in its family, so a slate built before it is stale even though the
        # waiting lots are identical.
        tool_dirty = self._sync_tools()

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
        if dirty is None:
            solve_lots = lots
        elif dirty:
            solve_lots = [l for l in lots if l.actual_step.family in dirty]
        else:
            solve_lots = []

        stats = {'assigned': 0, 'ready': len(lots), 'variables': 0,
                 'solve_time_s': 0.0, 'objective': 0.0, 'status': 'skipped',
                 'detail': 'no dirty family'}
        if solve_lots:
            payload = [self._lot_dict(l, t) for l in solve_lots]
            tokens, stats = self.planner.plan(
                payload, budget_s=self.budget_s, threads=self.threads)

            if dirty is None:
                self.token_of = {}
            else:
                # Drop stale tokens for the families being re-solved; every
                # other family keeps what it had.
                for lot in solve_lots:
                    self.token_of.pop(lot.idx, None)
            for lot_index, tool_id, alternate, rank, _exp in tokens:
                lot = solve_lots[lot_index]
                self.token_of[lot.idx] = (tool_id, alternate, rank)

        # A lot that has left the ready pool must not keep a token, or a stale
        # entry would answer for a lot that is already running.
        live = {l.idx for l in lots}
        if len(self.token_of) > len(live):
            self.token_of = {k: v for k, v in self.token_of.items() if k in live}

        self.by_tool = {}
        for lot_idx, (tool_id, _alt, rank) in self.token_of.items():
            self.by_tool.setdefault(tool_id, {})[lot_idx] = rank

        self.last_build_t = t
        self.builds += 1
        self.solve_time_s += stats['solve_time_s']
        self.last_stats = stats

    def _sync_tools(self):
        """Push only the tool state that moved; return the families it touched."""
        changed = 0
        touched = set()
        for i, m in enumerate(self._machines):
            cur = (m.current_setup or '',
                   int(m.min_runs_left or 0) if m.min_runs_left is not None else 0,
                   m.min_runs_setup or '')
            if self._tool_state.get(i) == cur:
                continue
            self._tool_state[i] = cur
            self.planner.set_tool_state(
                i, current_setup=cur[0], min_runs_left=cur[1],
                min_runs_setup=cur[2])
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
    def _lot_dict(self, lot, t):
        step = lot.actual_step
        return {
            'lot_id': str(lot.idx),
            'family': step.family,
            'setup_group': step.setup_needed or '',
            'step': step.step_name,           # batch key, part 1
            'part': lot.part_name,            # batch key, part 2
            'batch_min': int(step.batch_min or 1),
            'batch_max': int(step.batch_max or 1),
            'wafers': int(lot.pieces or 25),
            'priority': self._urgency(lot, t),
            'qtime_slack_s': QTIME_INERT,
            'step_process_s': step.processing_time.avg(),
            'due_s': lot.deadline_at,
            'waiting_s': max(0.0, t - (lot.free_since or t)),
        }

    def _urgency(self, lot, t):
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
        # date even with zero further queueing, so the curve is steep below 1
        # and flat above 2 where a lot has slack to spare.
        cr = lot.cr(t)
        u *= 1.0 + max(0.0, 2.0 - cr)

        # Ageing, so a lot cannot be starved indefinitely by a stream of more
        # urgent work. Deliberately weak: one week of queueing doubles it.
        u *= 1.0 + min(1.0, max(0.0, t - (lot.free_since or t)) / 604800.0)

        if self.pressure != 'full':
            return u

        # Tier 2 -- downstream congestion. None of fifo/cr/lifo look past the
        # current step. Pulling a lot into an already-congested next family
        # just moves the queue; feeding a starving one keeps a bottleneck fed.
        nxt = lot.remaining_steps[0] if lot.remaining_steps else None
        if nxt is not None:
            ahead = self._family_wip.get(nxt.family, 0)
            capacity = max(1, len(self.instance.family_machines.get(nxt.family, ())))
            load = ahead / capacity
            # load 0 (starving downstream) -> 1.25x, load >= 5 -> 0.8x.
            u *= max(0.8, 1.25 - 0.09 * min(load, 5.0))

        # Batch formation: a lot whose step batches and whose cohort is already
        # near the minimum is worth more, because dispatching it lets a furnace
        # fire instead of sitting half full. greedy.py already maximises batch
        # size within a tie; this makes the batch visible BEFORE the tie.
        step = lot.actual_step
        if step.batch_max and step.batch_max > 1:
            cohort = self._family_wip.get(step.family, 0)
            if cohort >= (step.batch_min or 1):
                u *= 1.1
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
            if str(machine.idx) in self.by_tool:
                self.decisions_covered += 1

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
            lot.ptuple = (gate, setup, -lot.priority, 3,
                          self._score(lot, time, machine, setup))
        return lot.ptuple

    def _score(self, lot, time, machine, setup):
        """The linearized form of SolverExporter::cost. Lower is better."""
        step = lot.actual_step
        proc = step.processing_time.avg()
        time_cost = (setup or 0.0) + proc
        return time_cost / max(self._urgency(lot, time), 0.01)

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
            'cycle_s': self.cycle_s,
            'builds': self.builds,
            'decisions': self.decisions,
            'coverage': round(cov, 4),
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
