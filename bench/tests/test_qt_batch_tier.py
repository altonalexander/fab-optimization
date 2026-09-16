#!/usr/bin/env python3
"""`qt` must protect queue-time windows at BATCH tools, not only single-lot ones.

greedy.py forms batches by sorting same-route-step groups on
(ptuple[0], fill, min-batch, *ptuple[2:]) -- it skips slot 1 because upstream
reserved it for a q-time tier they never enabled. For fifo and cr slot 1 is
setup time, so skipping it is harmless. For `qt` slot 1 IS the at-risk flag
and slot 2 is the slack rank, which is 0 for a lot with nothing to save and
POSITIVE for a lot that can still make its window. Sorted ascending without
the flag, the savable group goes LAST: the rule's window protection was off,
and inverted, at exactly the furnaces that carry the violations
(docs/notes/2026-09-16-scrap-band.md).

    one furnace F, batch exactly 2 lots, 120 min
    r2: 2 lots  F                  released t=0     -> occupy F 0..120
    r1: 2 lots  A 5 min -> F       released t=0     -> window 2 h opens at 5/10 min
    r2: 2 lots  F                  released t=1 min -> no window
    at t=120 F frees with both pairs fireable. Firing r2 first makes the r1
    pair start F at 240 > 125 -> scrapped. Firing r1 first saves it.

Run directly:  baselines/pyscfabsim/.venv/bin/python3 bench/tests/test_qt_batch_tier.py
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import test_cqt_mechanism as tm                        # noqa: E402  (sets sys.path)

from classes import Route, Lot, Step                   # noqa: E402
from instance import Instance                          # noqa: E402
from randomizer import Randomizer                      # noqa: E402
import sim_runner                                      # noqa: E402


def batch_step(i, fam, ptime_min, lots=2):
    d = {'STEP': i, 'DESC': f'{i}_{fam}', 'STNFAM': fam, 'SETUP': '', 'STIME': '', 'STUNITS': '',
         'RWKSTEP': '', 'PTPER': 'per_batch', 'PDIST': 'constant', 'PTUNITS': 'min',
         'PTIME': ptime_min, 'PTIME2': None, 'BATCHMN': 25 * lots, 'BATCHMX': 25 * lots,
         'StepPercent': '', 'REWORK': '', 'PartInterval': '', 'BatchInterval': '',
         'FORSTEP': '', 'SVESTN': ''}
    return Step(i - 1, 25, d, False)


def build():
    Randomizer().random.seed(0)
    ms = [tm.machine(0, 'A'), tm.machine(1, 'F')]
    r1 = Route('r1', [tm.step(1, 'A', 5, cqt_to=2, cqt_hr=2), batch_step(2, 'F', 120)])
    r2 = Route('r2', [batch_step(1, 'F', 120)])
    meta = lambda n: {'LOT': n, 'PART': 'p', 'PIECES': 25}
    lots = [Lot(0, r2, 10, 0, 10 * 86400, meta('B0')), Lot(1, r2, 10, 0, 10 * 86400, meta('B1')),
            Lot(2, r1, 10, 0, 10 * 86400, meta('W0')), Lot(3, r1, 10, 0, 10 * 86400, meta('W1')),
            Lot(4, r2, 10, 60, 10 * 86400, meta('B2')), Lot(5, r2, 10, 60, 10 * 86400, meta('B3')),
            Lot(6, r2, 10, tm.SENTINEL_S, 10 * 86400, meta('sentinel'))]
    inst = Instance(ms, {'r1': r1, 'r2': r2}, lots, {}, {}, [], True, [])
    inst.batch_strat, inst.rpt_route = 'Demand', None
    inst.cqt_enforce, inst.cqt_scale, inst.cqt_rework, inst.cqt_max_rework = True, 1.0, True, 0
    return inst


def run(rule):
    inst = build()
    sim_runner.run(inst, 2 * 86400, rule, stream=open(os.devnull, 'w'))
    return len(inst.done_lots), len(inst.scrapped_lots)


def test_qt_fires_the_savable_batch_first():
    assert run('qt') == (6, 0), run('qt')


def test_fifo_is_window_blind_and_scraps_the_pair():
    """The control: a rule that cannot see windows loses the pair here, so
    the scenario really does hinge on batch order."""
    assert run('fifo') == (4, 2), run('fifo')


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
