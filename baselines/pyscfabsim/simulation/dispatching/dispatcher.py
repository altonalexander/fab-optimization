import os
from collections import defaultdict

from classes import Lot, Machine
from randomizer import Randomizer

r = Randomizer()

# Fraction of its window a lot must have burned through before `qt` promotes
# it. 1.0 = promote any saveable at-risk lot (the original, and the default,
# so published rows are unchanged).
QT_PROMOTE_FRAC = float(os.getenv('QT_PROMOTE_FRAC', '1.0'))


class Dispatchers:
    
    @staticmethod
    def get_setup(new_setup, machine, actual_step_setup_time, setups):
        if new_setup != '' and machine.current_setup != new_setup:
            if actual_step_setup_time is not None:
                return actual_step_setup_time
            elif (machine.current_setup, new_setup) in setups:
                return setups[(machine.current_setup, new_setup)]
            elif ('', new_setup) in setups:
                return setups[('', new_setup)]
        return 0

    @staticmethod
    def fifo_ptuple_for_lot(lot: Lot, time, machine: Machine = None, setups=None):
        if machine is not None:
            lot.ptuple = (
                0 if machine.min_runs_left is None or machine.min_runs_setup == lot.actual_step.setup_needed else 1,
                #0 if lot.cqt_waiting is not None else 1,
                Dispatchers.get_setup(lot.actual_step.setup_needed, machine, lot.actual_step.setup_time, setups),
                -lot.priority, lot.free_since, lot.deadline_at,
            )
            return lot.ptuple
        else:
            return -lot.priority, lot.free_since, lot.deadline_at,

    @staticmethod
    def lifo_ptuple_for_lot(lot: Lot, time, machine: Machine = None, setups=None):
        if machine is not None:
            lot.ptuple = (
                0 if machine.min_runs_left is None or machine.min_runs_setup == lot.actual_step.setup_needed else 1,
                #0 if lot.cqt_waiting is not None else 1,
                Dispatchers.get_setup(lot.actual_step.setup_needed, machine, lot.actual_step.setup_time, setups),
                -lot.priority, -lot.free_since, lot.deadline_at,
            )
            return lot.ptuple
        else:
            return -lot.priority, -lot.free_since, lot.deadline_at,

    @staticmethod
    def lifo_ptuple_for_lot_vergammeln(lot: Lot, time, machine: Machine = None, setups=None):
        if machine is not None:
            lot.ptuple = (
                0 if machine.min_runs_left is None or machine.min_runs_setup == lot.actual_step.setup_needed else 1,
                #0 if lot.cqt_waiting is not None else 1,
                Dispatchers.get_setup(lot.actual_step.setup_needed, machine, lot.actual_step.setup_time, setups),
                -lot.priority, -lot.free_since, -lot.deadline_at,
            )
            return lot.ptuple
        else:
            return -lot.priority, -lot.free_since, -lot.deadline_at,

    @staticmethod
    def cr_ptuple_for_lot(lot: Lot, time, machine: Machine = None, setups=None):
        if machine is not None:
            lot.ptuple = (
                0 if machine.min_runs_left is None or machine.min_runs_setup == lot.actual_step.setup_needed else 1,
                #0 if lot.cqt_waiting is not None else 1,
                Dispatchers.get_setup(lot.actual_step.setup_needed, machine, lot.actual_step.setup_time, setups),
                -lot.priority, lot.cr(time),
            )
            return lot.ptuple
        else:
            return -lot.priority, lot.cr(time),

    @staticmethod
    def qt_slack(lot: Lot, time):
        """Seconds until this lot's open queue-time window lapses.

        A lot carries cqt_waiting/cqt_deadline from the moment the OPENING
        step dispatched until the closing step is reached (instance.py). So a
        lot sitting in a queue with cqt_waiting set is mid-window and at risk;
        everything else is not. Returns None when no window is open.
        """
        if lot.cqt_waiting is None or lot.cqt_deadline is None:
            return None
        return lot.cqt_deadline - time

    @staticmethod
    def qt_ptuple_for_lot(lot: Lot, time, machine: Machine = None, setups=None):
        """`cr`, with a queue-time tier in front of it.

        Deliberately identical to cr_ptuple_for_lot except for two inserted
        elements, so that `qt` minus `cr` isolates the cost and benefit of
        protecting q-time windows and nothing else.

        Upstream wrote this tier and left it commented out in every rule
        ("#0 if lot.cqt_waiting is not None else 1") -- reasonably, since it
        had nothing to act on while CQT was parsed and never enforced
        (adr/0008 §2). Two differences from their version:

          - it is ORDERED BY SLACK, not binary. Theirs put every at-risk lot
            ahead of every safe one and then stopped; with hundreds of open
            windows that says nothing about which of them is about to lapse.
          - it sits AHEAD OF SETUP, which is where they put it, and that is
            the aggressive choice: protecting a window is treated as worth a
            changeover. It is also the choice that makes the tradeoff visible
            rather than muffling it, which is the point of the rule.
        """
        # Only a lot that can STILL make its window is worth prioritising.
        # Measured on the warmed fab: of 978 lots holding an open window, 650
        # -- 66.5% -- were already past deadline, median -67h and the worst
        # -262h. Sorting by ascending slack put those at the FRONT of every
        # queue, so the rule spent the fab's capacity on work already
        # guaranteed to rework or scrap, ahead of lots that could be saved.
        # That is why qt cost nothing over 12 cold days (low WIP, few lapsed)
        # and 55% of throughput on a warmed fab.
        #
        # A blown window cannot be un-blown, so a lapsed lot has nothing left
        # to protect and takes its turn by cr like any other.
        slack = Dispatchers.qt_slack(lot, time)
        saveable = slack is not None and slack > 0
        # Promote only lots that are actually CLOSE to lapsing. The first
        # version promoted any saveable lot, whether it had twenty minutes or
        # two hundred hours left -- indiscriminate, and it disrupts setup and
        # batch grouping for lots that were never in danger. That is the same
        # defect found in the solver's q-time term (adr/0017 §12): a parameter
        # nobody chose. 1.0 reproduces the original behaviour exactly, so
        # every published qt row stands.
        if saveable and QT_PROMOTE_FRAC < 1.0:
            w = getattr(lot, 'cqt_window_s', None)
            if w:
                saveable = slack < QT_PROMOTE_FRAC * w
        at_risk = 0 if saveable else 1
        rank = slack if saveable else 0.0
        if machine is not None:
            lot.ptuple = (
                0 if machine.min_runs_left is None or machine.min_runs_setup == lot.actual_step.setup_needed else 1,
                at_risk, rank,
                Dispatchers.get_setup(lot.actual_step.setup_needed, machine, lot.actual_step.setup_time, setups),
                -lot.priority, lot.cr(time),
            )
            return lot.ptuple
        else:
            return at_risk, rank, -lot.priority, lot.cr(time),

    @staticmethod
    def random_ptuple_for_lot(lot: Lot, time, machine: Machine = None, setups=None):
        if machine is not None:
            return (
                0 if machine.min_runs_left is None or machine.min_runs_setup == lot.actual_step.setup_needed else 1,
                #0 if lot.cqt_waiting is not None else 1,
                r.random.uniform(0, 99999),
            )
        else:
            return r.random.uniform(0, 99999),


class FeedTheBatch:
    """`qtf`: qt, plus upstream promotion of lots that complete a stalled batch.

    docs/notes (batch fill, 2026-09-16): at tight windows the diffusion
    furnaces spend 120-165 tool-hours a day FREE with lots queued, because no
    same-route-step group has reached batch_min. Reordering at the furnace
    cannot fix that -- there is nothing fireable to order. The lever is
    upstream: a lot K or fewer steps short of a batch step whose group is
    waiting below its minimum should jump its current queue.

    Tier order: qt's window rescue first (a lot about to blow its own window
    still wins), then feed, then everything else in qt's order. Among feeders,
    the group with the most urgent member (earliest window deadline, else
    longest wait) goes first.

    Needs the instance, which the upstream rule signature does not carry;
    greedy.py sets `.instance` before each decision when `wants_instance`.
    """
    wants_instance = True
    batch_qtime_tier = True
    REFRESH_S = 600.0

    def __init__(self, lookahead=3):
        self.lookahead = lookahead
        self.instance = None
        self._deficit = {}           # group key -> urgency (lower = sooner)
        self._built_at = None
        self._next_batch = {}        # id(actual_step) -> step_name or None

    def bind(self, instance):
        """Attach to `instance`; a new fab (or a resumed pickle) drops caches."""
        if self.instance is not instance:
            self.instance = instance
            self._deficit, self._built_at, self._next_batch = {}, None, {}

    def _refresh(self, time):
        inst = self.instance
        if self._built_at is not None and time - self._built_at < self.REFRESH_S:
            return
        groups = defaultdict(list)
        seen = set()
        for m in inst.machines:
            for l in m.waiting_lots:
                st = l.actual_step
                if l.idx in seen or st is None or st.batch_max <= 1:
                    continue
                seen.add(l.idx)
                groups[(st.step_name, l.part_name)].append(l)
        deficit = {}
        for key, ls in groups.items():
            if len(ls) >= ls[0].actual_step.batch_min:
                continue
            urg = min((l.cqt_deadline for l in ls
                       if l.cqt_waiting is not None and l.cqt_deadline is not None
                       and l.actual_step.order == l.cqt_waiting and l.cqt_deadline > time),
                      default=None)
            deficit[key] = urg if urg is not None else min(l.free_since for l in ls) + 7 * 86400
        self._deficit, self._built_at = deficit, time

    def _feeds(self, lot):
        st = lot.actual_step
        k = id(st)
        if k not in self._next_batch:
            nb = None
            for s in lot.remaining_steps[:self.lookahead]:
                if s.batch_max > 1:
                    nb = s.step_name
                    break
            self._next_batch[k] = nb
        nb = self._next_batch[k]
        return None if nb is None else self._deficit.get((nb, lot.part_name))

    def __call__(self, lot, time, machine=None, setups=None):
        base = Dispatchers.qt_ptuple_for_lot(lot, time, machine, setups)
        if self.instance is None:
            return base
        self._refresh(time)
        urg = self._feeds(lot) if lot.actual_step is not None and lot.actual_step.batch_max <= 1 else None
        feed = (0, urg) if urg is not None else (1, 0.0)
        if machine is not None:
            lot.ptuple = base[:3] + feed + base[3:]
            return lot.ptuple
        return base[:2] + feed + base[2:]


QTF_LOOKAHEAD = int(os.getenv('QTF_LOOKAHEAD', '3'))


class QtWindowFire(FeedTheBatch):
    """`qtfw`: qtf, plus firing a batch BELOW batch_min to save a window.

    bench/results/bound_ab: with batch_min = 1 everywhere, qt's scrap share at
    window scale 3 falls from 35 % to 2 % -- nearly all tight-window scrap is
    lots waiting for batch partners. Real fabs run a minimum batch with
    exceptions; this is that exception, and nothing more:

      fire an underfilled same-route-step group when one of its lots is inside
      a live window with less than QTFW_SLACK_H hours left (default 2), or --
      optionally -- when its oldest lot has waited QTFW_MAXWAIT_H (default off).

    The cost is real: an underfilled furnace run spends a full cycle on fewer
    wafers (bound_ab scale 5: shipped 52.2 -> 49.5/day at batch_min = 1). That
    trade is what an optimiser should make better than this threshold.
    """

    def __init__(self, lookahead=3, slack_h=2.0, maxwait_h=None):
        super().__init__(lookahead)
        self.slack_s = slack_h * 3600.0
        self.maxwait_s = None if maxwait_h is None else maxwait_h * 3600.0

    def fire_partial(self, group, time):
        for l in group:
            if (l.cqt_waiting is not None and l.cqt_deadline is not None
                    and l.actual_step.order == l.cqt_waiting
                    and 0 < l.cqt_deadline - time < self.slack_s):
                return True
        if self.maxwait_s is not None:
            return time - min(l.free_since for l in group) >= self.maxwait_s
        return False

    def next_fire_time(self, groups, time):
        """Earliest future instant any waiting group would qualify, or None."""
        best = None
        for g in groups:
            for l in g:
                if (l.cqt_waiting is not None and l.cqt_deadline is not None
                        and l.actual_step.order == l.cqt_waiting):
                    t = l.cqt_deadline - self.slack_s + 1.0
                    if time < t < l.cqt_deadline:
                        best = t if best is None else min(best, t)
            if self.maxwait_s is not None:
                t = min(l.free_since for l in g) + self.maxwait_s
                if t > time:
                    best = t if best is None else min(best, t)
        return best


QTFW_SLACK_H = float(os.getenv('QTFW_SLACK_H', '2'))
QTFW_MAXWAIT_H = float(os.environ['QTFW_MAXWAIT_H']) if os.getenv('QTFW_MAXWAIT_H') else None

# Whether `qt`'s window tier reaches batch formation (greedy.py). Until
# 2026-09-16 it did not -- the batch key skipped slot 1 and read the slack rank
# inverted, so qt protected windows everywhere EXCEPT batch tools, where the
# violations concentrate. 1 (default) is the fix; 0 reproduces every qt row
# published before it. In the checkpoint key (sim_feed.qt_tuning_key).
QT_BATCH_TIER = os.getenv('QT_BATCH_TIER', '1') != '0'
Dispatchers.qt_ptuple_for_lot.batch_qtime_tier = QT_BATCH_TIER

QTF_RULE = FeedTheBatch(QTF_LOOKAHEAD)
QTF_RULE.batch_qtime_tier = QT_BATCH_TIER
QTFW_RULE = QtWindowFire(QTF_LOOKAHEAD, QTFW_SLACK_H, QTFW_MAXWAIT_H)
QTFW_RULE.batch_qtime_tier = QT_BATCH_TIER

dispatcher_map = {
    'fifo': Dispatchers.fifo_ptuple_for_lot,
    'lifo_org': Dispatchers.lifo_ptuple_for_lot_vergammeln,
    'lifo_anders': Dispatchers.lifo_ptuple_for_lot,
    'cr': Dispatchers.cr_ptuple_for_lot,
    'qt': Dispatchers.qt_ptuple_for_lot,
    'qtf': QTF_RULE,
    'qtfw': QTFW_RULE,
    'random': Dispatchers.random_ptuple_for_lot,
}
