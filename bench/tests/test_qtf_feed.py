#!/usr/bin/env python3
"""`qtf` pulls forward the lot that completes a stalled batch.

    furnace F: batch exactly 2 lots, 60 min.  tool P: single lot, 60 min.
    route rf: P -> F          (the batch-completing lot)
    route rx: P               (unrelated work on the same P tool)
    route rb: F               (one lot already waiting at F, alone)

    t=0: one rb lot waits at F (cannot fire, min 2). At t=1 s three rx lots and
    the rf lot (latest due date) reach P, so cr/qt runs rf LAST (P at 180),
    and F cannot fire before 180. qtf sees F's group short by one and
    the rf lot one step away: it runs rf first, and F fires at about 60.

Run directly:  baselines/pyscfabsim/.venv/bin/python3 bench/tests/test_qtf_feed.py
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
    ms = [tm.machine(0, 'P'), tm.machine(1, 'F')]
    # Batch groups are same step NAME + part, so rb's F step shares rf's name.
    rf = Route('rf', [tm.step(1, 'P', 60), batch_step(2, 'F', 60)])
    rb = Route('rb', [batch_step(2, 'F', 60)])
    rx = Route('rx', [tm.step(1, 'P', 60)])
    meta = lambda n: {'LOT': n, 'PART': 'p', 'PIECES': 25}
    far = 30 * 86400
    lots = [Lot(0, rb, 10, 0, far, meta('B')),
            Lot(1, rx, 10, 1, far, meta('X0')), Lot(2, rx, 10, 1, far, meta('X1')),
            Lot(3, rx, 10, 1, far, meta('X2')),
            Lot(4, rf, 10, 1, 3 * far, meta('F')),   # latest due -> cr runs it last
            Lot(5, rx, 10, tm.SENTINEL_S, far, meta('sentinel'))]
    inst = Instance(ms, {'rf': rf, 'rb': rb, 'rx': rx}, lots, {}, {}, [], True, [])
    inst.batch_strat, inst.rpt_route = 'Demand', None
    return inst


def batch_start(rule):
    inst = build()
    starts = []
    orig = inst.dispatch

    def spy(machine, lots):
        if machine.family == 'F':
            starts.append(inst.current_time)
        return orig(machine, lots)
    inst.dispatch = spy
    sim_runner.run(inst, 2 * 86400, rule, stream=open(os.devnull, 'w'))
    return starts[0] / 60 if starts else None, len(inst.done_lots)


def test_qt_leaves_the_furnace_waiting():
    assert batch_start('qt')[0] > 180, batch_start('qt')


def test_qtf_feeds_the_furnace_first():
    assert batch_start('qtf')[0] is not None and batch_start('qtf')[0] < 62, batch_start('qtf')


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
