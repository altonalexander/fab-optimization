"""crit -- a CP-SAT scheduler for the critical batch sections, qtfw everywhere else.

Why (docs/notes/2026-09-16-scrap-band.md, bench/results/batch_fill/): queue-time
scrap concentrates at a few diffusion furnace families, where batches of 3-5
same-route-step lots must be formed. A sort key decides one tool, one instant:
it cannot wait for a lot 40 minutes out, cannot trade one furnace's batch
against another's, cannot see that firing a partial group now strands the
window of a lot arriving next. That is a scheduling problem, so this schedules.

Every PLAN_S seconds, for each critical family, over HORIZON_S:

  lots      waiting at the family now, plus lots up to LOOKAHEAD steps away
            whose next batch step is there (ETA = remaining processing + moves
            + a per-step queue pad)
  tools     each furnace, free now or busy until its done-event
  batches   SLOTS optional batches per tool: a group choice, a start time,
            lot assignment; batch_min <= size <= batch_max (min HARD unless
            CRIT_UNDERFILL_W prices each missing lot, v3); start >= member ETAs; no overlap
  objective blown windows (x BLOWN_W) + waiting-lot start delay
            + unscheduled waiting lots (horizon + penalty)

Execution: when a furnace in a planned family asks for work, it runs its next
planned batch if the members are there and its start has come; if the plan
says WAIT (members still arriving), the tool is held idle -- the one thing no
sort key can do. Upstream members of planned batches are expedited (qtf's feed
tier, keyed by planned start). Anything the plan does not cover, or a plan
that reality has invalidated, falls back to qtf and triggers a replan.

A rule object for sim_runner.run(); greedy.py calls `.override()` first.
"""
import collections
import os
import time as _time

from ortools.sat.python import cp_model

from dispatching.dispatcher import QtWindowFire, QTF_LOOKAHEAD, QTFW_SLACK_H, QTFW_MAXWAIT_H

H = 3600.0


def _avg(dist):
    f = getattr(dist, 'avg', None)
    if f is not None:
        return f()
    return getattr(dist, 'p', 0.0)


class CritSched(QtWindowFire):
    # v2 defaults (bench/results/crit_ab v1 lost to qtf K6 at scale 5: solver
    # timeouts on big WIP, and furnaces held for members whose ETAs were
    # optimistic). Shorter reach, fewer slots, and holds only for members
    # that are genuinely close.
    PLAN_S = float(os.getenv('CRIT_PLAN_S', '1800'))
    HORIZON_S = float(os.getenv('CRIT_HORIZON_S', str(8 * H)))
    SLOTS = int(os.getenv('CRIT_SLOTS', '2'))
    LOOK = int(os.getenv('CRIT_LOOKAHEAD', '1'))
    PAD_S = float(os.getenv('CRIT_PAD_S', '1800'))     # queue pad per upstream step
    BUDGET_S = float(os.getenv('CRIT_BUDGET_S', '2.0'))
    HOLD_MAX_S = float(os.getenv('CRIT_HOLD_MAX_S', '2700'))
    BLOWN_W = 2000
    UNSCHED_W = 200
    # v3: under-min batches allowed at a price per missing lot (a furnace run
    # spent on fewer wafers). None = batch_min hard, as in v1/v2.
    UNDERFILL_W = (float(os.environ['CRIT_UNDERFILL_W'])
                   if os.getenv('CRIT_UNDERFILL_W') else None)
    UNDERFILL_MODE = os.getenv('CRIT_UNDERFILL_MODE', 'fixed')   # 'fixed' | 'load' (v4)
    MODEL = os.getenv('CRIT_MODEL', 'tool')                      # 'tool' (v1-v3) | 'family' (v4)
    GROUP_BATCHES = int(os.getenv('CRIT_GROUP_BATCHES', '2'))
    HYBRID = os.getenv('CRIT_HYBRID', '0') == '1'                 # v5: plan only under contention, never hold
    START_TOL_S = 120.0

    def __init__(self, families=None, lookahead=None):
        # Fallback everywhere the plan does not decide is qtfw (qtf + under-min
        # firing), the strongest rule, so the scheduler is judged against it.
        super().__init__(QTF_LOOKAHEAD if lookahead is None else lookahead,
                         QTFW_SLACK_H, QTFW_MAXWAIT_H)
        env = os.getenv('CRIT_FAMILIES')
        self.families = tuple(families or (env.split(',') if env else
                              ('Diffusion_FE_94', 'Diffusion_FE_120', 'Diffusion_BE_123')))
        self._reset_plan()
        self.stats = collections.Counter()

    def _reset_plan(self):
        self.plan = {}             # machine idx -> [(start, [lot idx])] sorted
        self.assigned = {}         # lot idx -> planned start
        self.planned_at = None
        self.parked = set()
        self.debug = {}
        self.eta = {}              # lot idx -> (family, eta at last plan)

    def bind(self, instance):
        if self.instance is not instance:
            super().bind(instance)
            self._reset_plan()

    # ---- planning ----------------------------------------------------------
    def _candidates(self, fam, now):
        """(lot, eta, group, batch step, deadline or None) heading to `fam`."""
        inst = self.instance
        out = []
        for lot in inst.active_lots:
            st = lot.actual_step
            if st is None:
                continue
            steps = [st] + lot.remaining_steps[:self.LOOK]
            eta = now
            for k, s in enumerate(steps):
                if s.family == fam and s.batch_max > 1:
                    dl = None
                    if (lot.cqt_waiting is not None and lot.cqt_deadline is not None
                            and s.order == lot.cqt_waiting and lot.cqt_deadline > now):
                        dl = lot.cqt_deadline
                    out.append((lot, eta, (s.step_name, lot.part_name), s, dl))
                    break
                if s.batch_max > 1 and k > 0:
                    break          # another batch step first: too uncertain
                p, tr = _avg(s.processing_time), _avg(s.transport_time)
                if k == 0 and lot not in self._waiting:
                    eta += 0.5 * p + tr            # in process here: about half done
                else:
                    eta += self.PAD_S + p + tr     # queued here (or later): wait + run
        return out

    def _replan(self, now):
        inst = self.instance
        self._waiting = {l for m in inst.machines for l in m.waiting_lots}
        t0 = _time.time()
        for fam in self.families:
            tools = inst.family_machines.get(fam, ())
            if not tools:
                continue
            cands = self._candidates(fam, now)
            if not cands:
                continue
            solve = self._solve_family if self.MODEL == 'family' else self._solve
            ok = solve(fam, tools, cands, now)
            if ok:
                # Replace this family's plan only on success: a solver timeout
                # keeps the previous plan rather than dropping to no plan.
                for t in tools:
                    self.plan.pop(t.idx, None)
                self.plan.pop(('F', fam), None)
                self.plan.update(ok[0])
                self.assigned = {k: v for k, v in self.assigned.items() if self.eta.get(k, (None,))[0] != fam}
                self.assigned.update(ok[1])
                for c in cands:
                    self.eta[c[0].idx] = (fam, c[1])
        self.planned_at = now
        self.stats['plans'] += 1
        self.stats['plan_wall_s'] += _time.time() - t0

    def _solve(self, fam, tools, cands, now):
        inst = self.instance
        hor = int(self.HORIZON_S // 60)
        m = cp_model.CpModel()
        groups = collections.defaultdict(list)
        for i, c in enumerate(cands):
            groups[c[2]].append(i)
        # only groups that can reach their minimum inside the horizon
        groups = {g: ix for g, ix in groups.items()
                  if self.UNDERFILL_W is not None or len(ix) >= cands[ix[0]][3].batch_min}
        if not groups:
            return {}, {}
        gl = list(groups)
        eta = [max(0, int((c[1] - now) // 60)) for c in cands]
        dur = {g: max(1, int(_avg(cands[groups[g][0]][3].processing_time) // 60)) for g in gl}
        batches = []
        underfill = []
        x = {}
        for tool in tools:
            if inst.free_machines[tool.idx]:
                avail = 0
            else:
                ends = [e.timestamp for e in tool.events if hasattr(e, 'timestamp')]
                avail = max(0, int((min(ends) - now) // 60)) if ends else 60
            if avail >= hor:
                # Busy past the horizon. v1 built NewIntVar(avail, hor) here,
                # an empty domain: MODEL_INVALID, and the family went unplanned
                # (the 300-400 "plan_fail" at scale 5 in crit_ab v1).
                continue
            ivs = []
            prev_start = None
            for b in range(self.SLOTS):
                a = m.NewBoolVar('')
                y = {g: m.NewBoolVar('') for g in gl}
                m.Add(sum(y.values()) == a)
                s = m.NewIntVar(avail, hor, '')
                d = m.NewIntVar(0, max(dur.values()), '')
                m.Add(d == sum(dur[g] * y[g] for g in gl))
                e = m.NewIntVar(avail, hor + max(dur.values()), '')
                ivs.append(m.NewOptionalIntervalVar(s, d, e, a, ''))
                if prev_start is not None:        # symmetry: slots in order
                    m.Add(s >= prev_start).OnlyEnforceIf(a)
                prev_start = s
                for g in gl:
                    members = groups[g]
                    for i in members:
                        if not cands[i][0] in self._waiting and cands[i][1] > now + self.HORIZON_S:
                            continue
                        xv = m.NewBoolVar('')
                        x[(i, len(batches))] = xv
                        m.AddImplication(xv, y[g])
                        m.Add(s >= eta[i]).OnlyEnforceIf(xv)
                    bs = [x[(i, len(batches))] for i in members if (i, len(batches)) in x]
                    step = cands[members[0]][3]
                    if self.UNDERFILL_W is None:
                        m.Add(sum(bs) >= step.batch_min).OnlyEnforceIf(y[g])
                    else:
                        short = m.NewIntVar(0, step.batch_min, '')
                        m.Add(sum(bs) + short >= step.batch_min).OnlyEnforceIf(y[g])
                        m.Add(sum(bs) >= 1).OnlyEnforceIf(y[g])
                        underfill.append(short)
                    m.Add(sum(bs) <= step.batch_max)
                batches.append((tool, s, a))
            m.AddNoOverlap(ivs)
        cost = []
        for i, c in enumerate(cands):
            xs = [v for (li, _), v in x.items() if li == i]
            if xs:
                m.Add(sum(xs) <= 1)
            sched = m.NewBoolVar('')
            m.Add(sum(xs) == sched) if xs else m.Add(sched == 0)
            waiting = c[0] in self._waiting and c[3] is c[0].actual_step
            if waiting:
                start_i = m.NewIntVar(0, hor, '')
                for (li, bi), v in x.items():
                    if li == i:
                        m.Add(start_i == batches[bi][1]).OnlyEnforceIf(v)
                m.Add(start_i == 0).OnlyEnforceIf(sched.Not())
                cost.append(start_i)
                cost.append(self.UNSCHED_W * sched.Not())
                cost.append(hor * sched.Not())
            if c[4] is not None:
                dl = int((c[4] - now) // 60)
                blown = m.NewBoolVar('')
                for (li, bi), v in x.items():
                    if li == i:
                        m.Add(batches[bi][1] <= dl).OnlyEnforceIf([v, blown.Not()])
                if dl < hor:
                    m.AddImplication(sched.Not(), blown)
                cost.append(self.BLOWN_W * blown)
        if underfill:
            price = self.UNDERFILL_W
            if self.UNDERFILL_MODE == 'load':
                # v4: a missing lot costs capacity only when capacity is
                # scarce. crit_ab showed no fixed price wins both regimes
                # (u600 needed at scale 5, u300 better at 3). Price by the
                # family's fireable backlog relative to what its free-soon
                # tools can absorb in one cycle.
                waiting = sum(1 for c in cands if c[0] in self._waiting and c[3] is c[0].actual_step)
                cap = sum(c[3].batch_max for c in cands[:1]) * max(1, len(tools))
                load = waiting / max(1, cap)
                price = self.UNDERFILL_W * min(4.0, max(0.25, 4.0 * load))
                self.debug.setdefault(fam, {})['underfill_price'] = round(price)
            cost.append(int(price) * sum(underfill))
        m.Minimize(sum(cost))
        solver = cp_model.CpSolver()
        solver.parameters.max_time_in_seconds = self.BUDGET_S
        solver.parameters.num_workers = 1
        t_solve = _time.time()
        res = solver.Solve(m)
        self.debug[fam] = {
            'cands': len(cands), 'waiting': sum(1 for c in cands if c[0] in self._waiting and c[3] is c[0].actual_step),
            'windowed': sum(1 for c in cands if c[4] is not None),
            'groups': len(gl), 'tools': len(tools),
            'free_tools': sum(1 for t in tools if inst.free_machines[t.idx]),
            'status': solver.StatusName(res), 'wall_s': round(_time.time() - t_solve, 2),
            'obj': solver.ObjectiveValue() if res in (cp_model.OPTIMAL, cp_model.FEASIBLE) else None,
        }
        if res not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            self.stats['plan_fail'] += 1
            return None
        per_tool = collections.defaultdict(list)
        assigned = {}
        for bi, (tool, s, a) in enumerate(batches):
            if not solver.Value(a):
                continue
            members = [cands[li][0].idx for (li, bj), v in x.items() if bj == bi and solver.Value(v)]
            if members:
                st = now + solver.Value(s) * 60
                per_tool[tool.idx].append((st, members))
                for li in members:
                    assigned[li] = st
        return {k: sorted(v) for k, v in per_tool.items()}, assigned

    def _solve_family(self, fam, tools, cands, now):
        """v4 model: batches belong to a group, tools are a shared capacity.

        The per-tool model gives every slot on every tool a group choice, so
        13 identical furnaces x 2 slots x ~20 groups is hugely symmetric: with
        under-min batches allowed, Diffusion_FE_94 returned UNKNOWN in 2 s at
        scales 5 and 3 (crit_debug), leaving the family on stale plans. Here
        each group gets up to GROUP_BATCHES optional batches, and one
        cumulative constraint says at most len(tools) run at once (busy tools
        enter as fixed intervals). Which furnace runs a batch is decided at
        execution: the first free one.
        """
        inst = self.instance
        hor = int(self.HORIZON_S // 60)
        m = cp_model.CpModel()
        groups = collections.defaultdict(list)
        for i, c in enumerate(cands):
            groups[c[2]].append(i)
        under = self.UNDERFILL_W is not None
        groups = {g: ix for g, ix in groups.items()
                  if under or len(ix) >= cands[ix[0]][3].batch_min}
        if not groups:
            return {}, {}
        eta = [max(0, int((c[1] - now) // 60)) for c in cands]
        ivs, demands = [], []
        maxdur = 1
        for tool in tools:
            if inst.free_machines[tool.idx]:
                continue
            ends = [e.timestamp for e in tool.events if hasattr(e, 'timestamp')]
            busy = max(1, int((min(ends) - now) // 60)) if ends else 60
            ivs.append(m.NewIntervalVar(0, busy, busy, ''))
            demands.append(1)
        batches = []                      # (group, start var, active var)
        x = {}
        underfill = []
        for g, members in groups.items():
            step = cands[members[0]][3]
            dur = max(1, int(_avg(step.processing_time) // 60))
            maxdur = max(maxdur, dur)
            nb = min(self.GROUP_BATCHES, max(1, len(members) // (1 if under else step.batch_min)))
            prev = None
            for b in range(nb):
                a = m.NewBoolVar('')
                s = m.NewIntVar(0, hor, '')
                ivs.append(m.NewOptionalIntervalVar(s, dur, s + dur, a, ''))
                demands.append(1)
                bi = len(batches)
                batches.append((g, s, a))
                xs = []
                for i in members:
                    xv = m.NewBoolVar('')
                    x[(i, bi)] = xv
                    xs.append(xv)
                    m.AddImplication(xv, a)
                    m.Add(s >= eta[i]).OnlyEnforceIf(xv)
                m.Add(sum(xs) <= step.batch_max)
                if under:
                    short = m.NewIntVar(0, step.batch_min, '')
                    m.Add(sum(xs) + short >= step.batch_min).OnlyEnforceIf(a)
                    m.Add(sum(xs) >= 1).OnlyEnforceIf(a)
                    underfill.append(short)
                else:
                    m.Add(sum(xs) >= step.batch_min).OnlyEnforceIf(a)
                if prev is not None:      # symmetry within a group
                    m.AddImplication(a, prev[1])
                    m.Add(s >= prev[0]).OnlyEnforceIf(a)
                prev = (s, a)
        m.AddCumulative(ivs, demands, len(tools))
        cost = []
        for i, c in enumerate(cands):
            xs = [v for (li, _), v in x.items() if li == i]
            if not xs:
                continue
            m.Add(sum(xs) <= 1)
            sched = m.NewBoolVar('')
            m.Add(sum(xs) == sched)
            if c[0] in self._waiting and c[3] is c[0].actual_step:
                start_i = m.NewIntVar(0, hor, '')
                for (li, bi), v in x.items():
                    if li == i:
                        m.Add(start_i == batches[bi][1]).OnlyEnforceIf(v)
                m.Add(start_i == 0).OnlyEnforceIf(sched.Not())
                cost += [start_i, (self.UNSCHED_W + hor) * sched.Not()]
            if c[4] is not None:
                dl = int((c[4] - now) // 60)
                blown = m.NewBoolVar('')
                for (li, bi), v in x.items():
                    if li == i:
                        m.Add(batches[bi][1] <= dl).OnlyEnforceIf([v, blown.Not()])
                if dl < hor:
                    m.AddImplication(sched.Not(), blown)
                cost.append(self.BLOWN_W * blown)
        if underfill:
            price = self.UNDERFILL_W
            if self.UNDERFILL_MODE == 'load':
                waiting = sum(1 for c in cands if c[0] in self._waiting and c[3] is c[0].actual_step)
                cap = cands[0][3].batch_max * max(1, len(tools))
                price = self.UNDERFILL_W * min(4.0, max(0.25, 4.0 * waiting / max(1, cap)))
            cost.append(int(price) * sum(underfill))
        m.Minimize(sum(cost))
        solver = cp_model.CpSolver()
        solver.parameters.max_time_in_seconds = self.BUDGET_S
        solver.parameters.num_workers = 1
        t_solve = _time.time()
        res = solver.Solve(m)
        self.debug[fam] = {
            'model': 'family', 'cands': len(cands), 'groups': len(groups), 'batches': len(batches),
            'tools': len(tools), 'free_tools': sum(1 for t in tools if inst.free_machines[t.idx]),
            'status': solver.StatusName(res), 'wall_s': round(_time.time() - t_solve, 2),
            'obj': solver.ObjectiveValue() if res in (cp_model.OPTIMAL, cp_model.FEASIBLE) else None,
        }
        if res not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            self.stats['plan_fail'] += 1
            return None
        queue, assigned = [], {}
        for bi, (g, s, a) in enumerate(batches):
            if not solver.Value(a):
                continue
            members = [cands[li][0].idx for (li, bj), v in x.items() if bj == bi and solver.Value(v)]
            if members:
                st = now + solver.Value(s) * 60
                queue.append((st, members))
                for li in members:
                    assigned[li] = st
        return {('F', fam): sorted(queue)}, assigned

    # ---- execution -----------------------------------------------------------
    def override(self, instance, machine):
        """None = let the rule decide; [] = hold this tool; [lots] = run them."""
        self.bind(instance)
        if machine.family not in self.families:
            return None
        now = instance.current_time
        if self.planned_at is None or now - self.planned_at >= self.PLAN_S:
            self._replan(now)
        if self.MODEL == 'family':
            return self._override_family(instance, machine, now)
        queue = self.plan.get(machine.idx)
        if not queue:
            self.stats['fallback_noplan'] += 1
            return None
        start, members = queue[0]
        by_idx = {l.idx: l for l in machine.waiting_lots}
        here = [by_idx[i] for i in members if i in by_idx]
        if now + self.START_TOL_S < start:
            if start - now > self.HOLD_MAX_S:
                # Too far out to justify idling a furnace: let the rule use it.
                self.stats['fallback_far_start'] += 1
                return None
            self.stats['hold_wait_start'] += 1
            return []
        if len(here) == len(members):
            queue.pop(0)
            self.stats['planned_batches'] += 1
            return here
        missing_due = [self.eta.get(i, (None, float('inf')))[1] for i in members if i not in by_idx]
        close = any(e <= now + self.HOLD_MAX_S for e in missing_due)
        if close and now - start < self.HOLD_MAX_S:
            self.stats['hold_wait_members'] += 1
            return []
        step = here[0].actual_step if here else None
        if step is not None and len(here) >= step.batch_min:
            queue.pop(0)
            self.stats['partial_batches'] += 1
            return here[:step.batch_max]
        # plan is stale for this tool: drop it and let the rule act
        self.plan.pop(machine.idx, None)
        self.stats['fallback_stale'] += 1
        return None

    def _contended(self, instance, machine, now):
        """v5: are there more at-risk windowed groups than free furnaces?

        On fair warm-ups no planning variant beat qtfw outright (crit_v4_ab):
        the rule already fires at-risk groups first and underfilled when
        needed. The one decision it makes myopically is WHICH at-risk groups
        get the furnaces when there are not enough to go round -- it takes
        the head lot's slack, not how many windows each run saves or what
        arrives next. So the hybrid lets the plan decide only then.
        """
        groups = set()
        for l in machine.waiting_lots:
            st = l.actual_step
            if (l.cqt_waiting is not None and l.cqt_deadline is not None
                    and st is not None and st.order == l.cqt_waiting
                    and 0 < l.cqt_deadline - now < self.slack_s):
                groups.add((st.step_name, l.part_name))
        free = sum(1 for t in instance.family_machines[machine.family]
                   if instance.free_machines[t.idx])
        return len(groups) > free

    def _override_family(self, instance, machine, now):
        queue = self.plan.get(('F', machine.family))
        if not queue:
            self.stats['fallback_noplan'] += 1
            return None
        if self.HYBRID:
            if not self._contended(instance, machine, now):
                self.stats['hybrid_rule'] += 1
                return None
            self.stats['hybrid_plan'] += 1
        by_idx = {l.idx: l for l in machine.waiting_lots}
        # Hold budget: a free furnace may idle for a batch due soon only if
        # fewer furnaces of the family are already idling than batches are due.
        due_soon = sum(1 for st, _ in queue if st <= now + self.HOLD_MAX_S)
        parked = self.instance._hold_state()['parked']
        idling = sum(1 for t in self.instance.family_machines[machine.family]
                     if t in parked and t is not machine and self.instance.free_machines[t.idx])
        for k, (start, members) in enumerate(queue):
            if start > now + self.START_TOL_S:
                break
            here = [by_idx[i] for i in members if i in by_idx]
            if len(here) == len(members):
                queue.pop(k)
                self.stats['planned_batches'] += 1
                return here
            missing_due = [self.eta.get(i, (None, float('inf')))[1] for i in members if i not in by_idx]
            if any(e <= now + self.HOLD_MAX_S for e in missing_due) and now - start < self.HOLD_MAX_S:
                continue                  # members still coming: try the next batch
            if here:
                queue.pop(k)
                self.stats['partial_batches'] += 1
                return here[:here[0].actual_step.batch_max]
        if (not self.HYBRID and due_soon > idling and queue
                and queue[0][0] - now <= self.HOLD_MAX_S):
            self.stats['hold_wait'] += 1
            return []
        self.stats['fallback_rule'] += 1
        return None

    def _feeds(self, lot):
        if self.HYBRID:
            return super()._feeds(lot)   # v5 leaves upstream expediting to the rule
        st = self.assigned.get(lot.idx)
        if st is not None and lot.actual_step is not None and lot.actual_step.family not in self.families:
            return st
        return super()._feeds(lot)
