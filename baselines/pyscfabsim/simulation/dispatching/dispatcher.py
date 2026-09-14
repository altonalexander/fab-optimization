import os

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


dispatcher_map = {
    'fifo': Dispatchers.fifo_ptuple_for_lot,
    'lifo_org': Dispatchers.lifo_ptuple_for_lot_vergammeln,
    'lifo_anders': Dispatchers.lifo_ptuple_for_lot,
    'cr': Dispatchers.cr_ptuple_for_lot,
    'qt': Dispatchers.qt_ptuple_for_lot,
    'random': Dispatchers.random_ptuple_for_lot,
}
