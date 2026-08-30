"""
t_sim_runner -- the loop contract, without the simulator.

sim_runner.run() is the loop that tool_probe.py and sim_feed.py each used to
carry a copy of, so a regression here breaks both the measurement and the
dashboard feed at once. The baseline needs pandas and a dataset and takes
minutes; this needs neither. It drives run() against a fake instance and pins
the things the callers depend on:

  both break conditions (no next decision point, and past run_to)
  lots-is-None removes the machine from the usable set instead of dispatching
  hook ordering around the dispatch
  Ctrl-C is summarised and reported, never raised, with partial work intact

Run it with any interpreter:  python3 bench/tools/t_sim_runner.py
"""
import io
import os
import sys
import types

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Stand in for the vendored baseline. run() imports these lazily, so they only
# have to exist by the time it is called -- which is what lets this test run
# outside baselines/pyscfabsim/.venv.
_disp = types.ModuleType('dispatching.dispatcher')
_disp.dispatcher_map = {'fifo': 'FIFO_RULE'}
_pkg = types.ModuleType('dispatching')
_pkg.dispatcher = _disp
_greedy = types.ModuleType('greedy')
sys.modules.update({'dispatching': _pkg, 'dispatching.dispatcher': _disp,
                    'greedy': _greedy})

import sim_runner  # noqa: E402

FAILS = []


def check(name, got, want):
    if got == want:
        print(f'  ok  {name}')
    else:
        FAILS.append(name)
        print(f'  FAIL {name}: got {got!r}, want {want!r}')


class Machine:
    def __init__(self, idx):
        self.idx = idx


class FakeInstance:
    """One decision point per simulated second, then done."""

    def __init__(self, points, interrupt_at=None, starve_on=()):
        self.done = False
        self.current_time = 0
        self.points = points
        self.calls = 0
        self.interrupt_at = interrupt_at
        self.starve_on = set(starve_on)
        self.usable_machines = set()
        self.dispatched = []

    def next_decision_point(self):
        if self.calls >= self.points:
            return True
        self.current_time = self.calls
        self.calls += 1
        return False

    def dispatch(self, machine, lots):
        self.dispatched.append(machine.idx)


def rule_for(inst):
    """Stands in for greedy.get_lots_to_dispatch_by_machine."""
    def pick(instance, rule):
        assert rule == 'FIFO_RULE', f'dispatcher not resolved: {rule!r}'
        i = instance.calls - 1
        m = Machine(i)
        instance.usable_machines.add(m)
        if instance.interrupt_at is not None and i >= instance.interrupt_at:
            raise KeyboardInterrupt
        return (m, None) if i in instance.starve_on else (m, ['lot'])
    _greedy.get_lots_to_dispatch_by_machine = pick


def main():
    # A run that dispatches at every decision point.
    inst = FakeInstance(5)
    rule_for(inst)
    seen = []
    interrupted = sim_runner.run(
        inst, run_to=10000, dispatcher='fifo',
        before_dispatch=lambda i: seen.append(('before', i.current_time)),
        after_dispatch=lambda i, m, d: seen.append(('after', m.idx, d)))
    check('clean run reports no interrupt', interrupted, False)
    check('dispatches at every point', inst.dispatched, [0, 1, 2, 3, 4])
    check('before fires ahead of the dispatch it belongs to', seen[:3],
          [('before', 0), ('after', 0, True), ('before', 1)])

    # No lots for the machine: it leaves the usable set and nothing dispatches.
    inst = FakeInstance(4, starve_on=(1, 2))
    rule_for(inst)
    flags = []
    sim_runner.run(inst, run_to=10000, dispatcher='fifo',
                   after_dispatch=lambda i, m, d: flags.append(d))
    check('only dispatchable machines dispatch', inst.dispatched, [0, 3])
    check('machines with no lots stay removed', len(inst.usable_machines), 2)
    check('after_dispatch is told which happened', flags,
          [True, False, False, True])

    # run_to ends the run even when decision points remain. The check is
    # `current_time > run_to`, tested before the dispatch, so the point that
    # crosses the line is not dispatched: day 4 > run_to 3 stops it at 3.
    inst = FakeInstance(100)
    rule_for(inst)
    sim_runner.run(inst, run_to=3, dispatcher='fifo')
    check('run_to ends the run', inst.dispatched, [0, 1, 2, 3])

    # Ctrl-C during a long run: summarised, reported, partial work kept.
    inst = FakeInstance(100, interrupt_at=2)
    rule_for(inst)
    buf = io.StringIO()
    interrupted = sim_runner.run(inst, run_to=10000, dispatcher='fifo',
                                 stream=buf)
    check('interrupt is reported to the caller', interrupted, True)
    check('interrupt is summarised, not raised',
          'stopped at day' in buf.getvalue(), True)
    check('work done before the interrupt survives', inst.dispatched, [0, 1])

    # Constants the callers rely on agreeing about.
    check('RESET_AT matches greedy.py (one year)',
          sim_runner.RESET_AT, 365 * 86400)
    check('SECONDS_PER_DAY', sim_runner.SECONDS_PER_DAY, 86400)
    check('bare dataset name is qualified',
          sim_runner.normalize_dataset('LVHM'), 'SMT2020_LVHM')
    check('qualified dataset name is left alone',
          sim_runner.normalize_dataset('SMT2020_HVLM'), 'SMT2020_HVLM')

    print()
    if FAILS:
        print(f'  {len(FAILS)} FAILED')
        return 1
    print('  sim_runner ok')
    return 0


if __name__ == '__main__':
    sys.exit(main())
