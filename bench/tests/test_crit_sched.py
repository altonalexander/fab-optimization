#!/usr/bin/env python3
"""The critical-section scheduler holds a furnace for a lot still on its way.

    furnace F: one tool, batch exactly 2 lots, 60 min
    W1: P (1 min) -> E (1 min, opens a 50 min window to F) -> F
        at F by t=2 min, window deadline t=52
    W2: P2 (30 min) -> E -> F      at F by t=31, same step+part group as W1
    A1, A2: F only, a different part, released t=3 -> fireable at once

    qt   at t=3 fires A1+A2 (the only fireable group); F is busy to 63; W1+W2
         start at 63 > 52 -> W1 blown -> scrapped.
    crit plans at t=2: W1+W2 at ~33, holds F through t=3 when A1+A2 arrive,
         fires the W group at 31-33, then A at 91 -> nothing scrapped.

Run directly:  baselines/pyscfabsim/.venv/bin/python3 bench/tests/test_crit_sched.py
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, '..', 'tools'))
import test_cqt_mechanism as tm                        # noqa: E402
from test_qt_batch_tier import batch_step              # noqa: E402

from classes import Route, Lot                         # noqa: E402
from instance import Instance                          # noqa: E402
from randomizer import Randomizer                      # noqa: E402
import sim_runner                                      # noqa: E402
import crit_sched                                      # noqa: E402


def build():
    Randomizer().random.seed(0)
    ms = [tm.machine(0, 'P'), tm.machine(1, 'P2'), tm.machine(2, 'E'), tm.machine(3, 'F')]
    win = 50 / 60
    rw1 = Route('rw1', [tm.step(1, 'P', 1), tm.step(2, 'E', 1, cqt_to=3, cqt_hr=win), batch_step(3, 'F', 60)])
    rw2 = Route('rw2', [tm.step(1, 'P2', 30), tm.step(2, 'E', 1, cqt_to=3, cqt_hr=win), batch_step(3, 'F', 60)])
    ra = Route('ra', [batch_step(3, 'F', 60)])
    far = 30 * 86400
    lot = lambda i, r, rel, part, n: Lot(i, r, 10, rel, far, {'LOT': n, 'PART': part, 'PIECES': 25})
    lots = [lot(0, rw1, 0, 'p', 'W1'), lot(1, rw2, 0, 'p', 'W2'),
            lot(2, ra, 180, 'q', 'A1'), lot(3, ra, 180, 'q', 'A2'),
            lot(4, ra, tm.SENTINEL_S, 'q', 'sentinel')]
    inst = Instance(ms, {'rw1': rw1, 'rw2': rw2, 'ra': ra}, lots, {}, {}, [], True, [])
    inst.batch_strat, inst.rpt_route = 'Demand', None
    inst.cqt_enforce, inst.cqt_scale, inst.cqt_rework, inst.cqt_max_rework = True, 1.0, True, 0
    return inst


def run(rule):
    inst = build()
    sim_runner.run(inst, 2 * 86400, rule, stream=open(os.devnull, 'w'))
    return len(inst.done_lots), len(inst.scrapped_lots)


def crit_rule():
    r = crit_sched.CritSched(families=('F',))
    r.PAD_S = 0.0
    r.LOOK = 2            # W2 is two steps (P2, E) from the furnace
    r.BUDGET_S = 5.0
    return r


def test_qt_blows_the_window():
    assert run('qt') == (3, 1), run('qt')


def test_crit_holds_the_furnace_and_saves_it():
    rule = crit_rule()
    out = run(rule)
    assert out == (4, 0), (out, dict(rule.stats))
    assert rule.stats['hold_wait_start'] + rule.stats['hold_wait_members'] > 0, dict(rule.stats)


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
