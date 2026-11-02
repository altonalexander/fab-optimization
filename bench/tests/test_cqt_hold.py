#!/usr/bin/env python3
"""Synthetic tests for hold-before-entry (Instance.hold_blocks).

Two routes on a tiny fab, driven through the real event loop:

    r1 (6 lots)   step 1  family A   5 min   <- entrance: 60 min window to step 2
                  step 2  family C  30 min   <- exit
    r2 (12 lots)  step 1  family C  30 min   <- background load on the exit tool

Everything is released at t = 0 and dispatched FIFO, so the twelve background
lots own the single C tool for six hours. Without a hold the r1 lots run A at
once, open their windows, queue behind that backlog and every one is scrapped.
With a hold they wait at A -- where waiting costs nothing -- until C's queue
is short enough to meet the window.

Run directly:  baselines/pyscfabsim/.venv/bin/python3 bench/tests/test_cqt_hold.py
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import test_cqt_mechanism as tm                        # noqa: E402  (sets sys.path)

from classes import Route, Lot                         # noqa: E402
from instance import Instance                          # noqa: E402
from randomizer import Randomizer                      # noqa: E402

N_WIN, N_BG = 6, 12


def build(hold_frac, hold_max_h=24):
    Randomizer().random.seed(0)
    ms = [tm.machine(0, 'A'), tm.machine(1, 'C')]
    r1 = Route('r1', [tm.step(1, 'A', 5, cqt_to=2, cqt_hr=1), tm.step(2, 'C', 30)])
    r2 = Route('r2', [tm.step(1, 'C', 30)])
    lots = [Lot(i, r2, 10, 0, 10 * 86400, {'LOT': f'B{i}', 'PART': 'p', 'PIECES': 25})
            for i in range(N_BG)]
    # Released after C has started two jobs, so the gate has a rate to go on
    # (a warmed fab always does) and FIFO still puts the background ahead.
    lots += [Lot(N_BG + i, r1, 10, 31 * tm.MIN, 10 * 86400, {'LOT': f'W{i}', 'PART': 'p', 'PIECES': 25})
             for i in range(N_WIN)]
    lots.append(Lot(N_BG + N_WIN, r2, 10, tm.SENTINEL_S, 10 * 86400,
                    {'LOT': 'sentinel', 'PART': 'p', 'PIECES': 25}))
    inst = Instance(ms, {'r1': r1, 'r2': r2}, lots, {}, {}, [], True, [])
    inst.batch_strat, inst.rpt_route = 'Demand', None
    inst.cqt_enforce, inst.cqt_scale, inst.cqt_rework, inst.cqt_max_rework = True, 1.0, True, 0
    inst.cqt_hold_frac = hold_frac
    inst.cqt_hold_max_s = hold_max_h * 3600.0
    inst.HOLD_RATE_MIN = 2
    return inst


def outcome(inst):
    return len(inst.done_lots), len(inst.scrapped_lots), inst.counter_cqt_violated


def test_without_hold_the_backlog_scraps_the_windowed_lots():
    done, scrapped, _ = outcome(tm.run(build(None)))
    assert (done, scrapped) == (N_BG, N_WIN), (done, scrapped)


def test_hold_saves_windowed_lots_and_strands_none():
    inst = tm.run(build(0.5))
    done, scrapped, _ = outcome(inst)
    assert done + scrapped == N_BG + N_WIN, (done, scrapped)   # nothing stranded
    assert scrapped < N_WIN // 2, (done, scrapped)
    assert inst._hold_state()['held'] > 0


def test_hold_cap_releases_lots_even_when_the_exit_stays_backed_up():
    """A 1-hour cap against a 6-hour backlog: the hold delays, then gives up,
    and the lots are released into the queue rather than kept forever."""
    inst = tm.run(build(0.5, hold_max_h=1))
    done, scrapped, _ = outcome(inst)
    assert done + scrapped == N_BG + N_WIN, (done, scrapped)
    assert scrapped > 0


def test_hold_is_inert_without_enforcement():
    inst = build(0.5)
    inst.cqt_enforce = False
    tm.run(inst)
    assert inst._hold_state()['held'] == 0


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
