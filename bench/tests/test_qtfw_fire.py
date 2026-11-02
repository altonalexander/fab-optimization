#!/usr/bin/env python3
"""`qtfw` fires an underfilled batch to save a window; qt and qtf never do.

    furnace F: batch exactly 2 lots, 60 min
    W: E (1 min, opens a 3 h window to F) -> F     -- no partner ever comes

    qt / qtf: F never reaches batch_min, W waits past its deadline and is
              still waiting at the end (not done).
    qtfw:     once W has < 2 h of window left (t ~ 1 h) it fires alone -> done,
              no violation.

Run directly:  baselines/pyscfabsim/.venv/bin/python3 bench/tests/test_qtfw_fire.py
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import test_cqt_mechanism as tm                        # noqa: E402
from test_qt_batch_tier import batch_step              # noqa: E402

from classes import Route, Lot                         # noqa: E402
from instance import Instance                          # noqa: E402
from randomizer import Randomizer                      # noqa: E402
import sim_runner                                      # noqa: E402


def build():
    Randomizer().random.seed(0)
    ms = [tm.machine(0, 'E'), tm.machine(1, 'F')]
    r = Route('r', [tm.step(1, 'E', 1, cqt_to=2, cqt_hr=3), batch_step(2, 'F', 60)])
    far = 30 * 86400
    lots = [Lot(0, r, 10, 0, far, {'LOT': 'W', 'PART': 'p', 'PIECES': 25}),
            Lot(1, r, 10, tm.SENTINEL_S, far, {'LOT': 'sentinel', 'PART': 'p', 'PIECES': 25})]
    inst = Instance(ms, {'r': r}, lots, {}, {}, [], True, [])
    inst.batch_strat, inst.rpt_route = 'Demand', None
    inst.cqt_enforce, inst.cqt_scale, inst.cqt_rework, inst.cqt_max_rework = True, 1.0, True, 0
    return inst


def run(rule):
    inst = build()
    sim_runner.run(inst, 1 * 86400, rule, stream=open(os.devnull, 'w'))
    return len(inst.done_lots), inst.counter_cqt_violated


def test_qt_never_fires_the_lone_lot():
    assert run('qt') == (0, 0), run('qt')


def test_qtf_never_fires_the_lone_lot():
    assert run('qtf') == (0, 0), run('qtf')


def test_qtfw_fires_underfilled_to_save_the_window():
    assert run('qtfw') == (1, 0), run('qtfw')


def main():
    tests = [(k, v) for k, v in sorted(globals().items()) if k.startswith('test_')]
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print(f'  ok    {name}')
        except AssertionError as e:
            failed += 1
            print(f'  FAIL  {name}: {e}')
    print(f'{len(tests) - failed}/{len(tests)} passed')
    sys.exit(1 if failed else 0)


if __name__ == '__main__':
    main()
