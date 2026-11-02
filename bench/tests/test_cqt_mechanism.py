#!/usr/bin/env python3
"""Synthetic tests for the queue-time mechanism (ADR 0016, docs/audit).

A three-step route on a tiny fab, driven through the REAL event loop, the
real dispatcher and the real `Instance` code -- nothing mocked -- so that
each test pins one sentence of the SMT2020 definition (Kopp et al., WSC
2020, §2.1): "A CQT violation occurs if the time span between the
COMPLETION of the entrance step and the BEGIN of processing of the exit step
is longer than the prescribed CQT."

    step 1  family A  10 min   <- entrance: opens a 60 min window to step 3
    step 2  family B  W  min   <- the only thing between them; W is the knob
    step 3  family A  10 min   <- exit: window is checked when it STARTS

With one tool per family, no transport, no setups and no breakdowns, the
span between step 1 completing and step 3 starting is exactly W (plus
load/unload, which are zero here). So:

    W = 55  ->  span 55 < 60  ->  NO violation   (old definition: 10+55 = 65 -> violation)
    W = 65  ->  span 65 > 60  ->  violation

The first case is the one the 2026-09-15 fix changed; it fails on the old
code. Run directly:  baselines/pyscfabsim/.venv/bin/python3 bench/tests/test_cqt_mechanism.py
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, '..', '..'))
for p in ('bench/tools', 'baselines/pyscfabsim', 'baselines/pyscfabsim/simulation'):
    sys.path.insert(0, os.path.join(REPO, p))
os.environ.setdefault('SIM_CONTROL_FILE', '/dev/null')

from classes import Machine, Step, Route, Lot          # noqa: E402
from instance import Instance                          # noqa: E402
from randomizer import Randomizer                      # noqa: E402
import sim_runner                                      # noqa: E402

MIN = 60.0
SENTINEL_S = 1000 * 86400.0


def machine(idx, fam):
    return Machine(idx, {'LTIME': 0, 'LTUNITS': 'min', 'ULTIME': 0, 'ULTUNITS': 'min',
                         'STNGRP': fam, 'STNFAMLOC': 'Fab', 'STNFAM': fam, 'STNCAP': '',
                         'WAKERESRANK': ''}, speed=1.0)


def step(i, fam, ptime_min, cqt_to=None, cqt_hr=None, sampling='', rework='', rwkstep=''):
    d = {'STEP': i, 'DESC': f'{i}_{fam}', 'STNFAM': fam, 'SETUP': '', 'STIME': '', 'STUNITS': '',
         'RWKSTEP': rwkstep, 'PTPER': 'per_lot', 'PDIST': 'constant', 'PTUNITS': 'min',
         'PTIME': ptime_min, 'PTIME2': None, 'BATCHMN': '', 'BATCHMX': '', 'StepPercent': sampling,
         'REWORK': rework, 'PartInterval': '', 'BatchInterval': '', 'FORSTEP': '', 'SVESTN': ''}
    if cqt_to is not None:
        d['STEP_CQT'] = cqt_to
        d['CQT'] = cqt_hr
        d['CQTUNITS'] = 'hr'
    return Step(i - 1, 25, d, False)


def build(w_min, *, enforce=True, rework=True, max_rework=3, scale=1.0, skip_entrance=False,
          n_lots=1, release_gap_min=0):
    Randomizer().random.seed(0)
    ms = [machine(0, 'A'), machine(1, 'B')]
    steps = [step(1, 'A', 10, cqt_to=3, cqt_hr=1, sampling=('0' if skip_entrance else '')),
             step(2, 'B', w_min),
             step(3, 'A', 10)]
    route = Route('r', steps)
    lots = [Lot(i, route, 10, i * release_gap_min * MIN, 10 * 86400, {'LOT': f'L{i}', 'PART': 'p', 'PIECES': 25})
            for i in range(n_lots)]
    # A sentinel release far past the horizon. Instance.process_until_calc()
    # treats an EMPTY release list as "process until t = 0", so with no
    # future release the clock never advances and the loop spins; the real
    # dataset never runs its release list dry inside a run.
    lots.append(Lot(n_lots, route, 10, SENTINEL_S, 10 * 86400, {'LOT': 'sentinel', 'PART': 'p', 'PIECES': 25}))
    inst = Instance(ms, {'r': route}, lots, {}, {}, [], True, [])
    inst.batch_strat, inst.rpt_route = 'Demand', None
    inst.cqt_enforce, inst.cqt_scale, inst.cqt_rework, inst.cqt_max_rework = enforce, scale, rework, max_rework
    return inst


def run(inst, days=3):
    sim_runner.run(inst, days * 86400, 'fifo', stream=open(os.devnull, 'w'))
    return inst


def counters(inst):
    return (inst.counter_cqt_violated, inst.counter_cqt_rework, inst.counter_cqt_scrapped,
            len(inst.done_lots), len(inst.scrapped_lots))


# --- the tests -------------------------------------------------------------
def test_window_opens_at_completion_not_start():
    """W = 55: span 55 min < 60 min window. The OLD definition charged the
    entrance step's own 10 min and would report a violation here."""
    inst = run(build(55))
    assert counters(inst) == (0, 0, 0, 1, 0), counters(inst)


def test_violation_when_span_exceeds_window():
    inst = run(build(65, rework=False))
    assert counters(inst) == (1, 0, 0, 1, 0), counters(inst)


def test_boundary_is_strict_greater():
    """span == window is NOT a violation (`current_time > deadline`)."""
    inst = run(build(60, rework=False))
    assert counters(inst) == (0, 0, 0, 1, 0), counters(inst)


def test_scale_multiplies_window():
    """Scale 2: a 60 min window becomes 120; W = 65 no longer violates."""
    inst = run(build(65, rework=False, scale=2.0))
    assert counters(inst) == (0, 0, 0, 1, 0), counters(inst)


def test_rework_returns_to_entrance_and_scraps_at_cap():
    """W = 65 violates every pass. Rework sends the lot back to step 1, it
    violates again, and after `cqt_max_rework` reworks the next violation
    scraps it: 4 violations, 3 reworks, 1 scrapped, 0 completed."""
    inst = run(build(65, rework=True, max_rework=3))
    assert counters(inst) == (4, 3, 1, 0, 1), counters(inst)
    lot = inst.scrapped_lots[0]
    assert lot.cqt_scrapped and lot.scrapped_at is not None
    assert lot not in inst.done_lots and lot not in inst.active_lots


def test_scrap_on_first_violation():
    """`--cqt-max-rework 0`: the semantics of the testbed's own queue-time
    paper (WSC 2020, violated lots "have to be scrapped"). One violation, NO
    rework, the lot leaves as scrap. Until 2026-09-16 the CLI read 0 as
    unbounded, so this could not be expressed at all (audit F4)."""
    inst = run(build(65, rework=True, max_rework=0))
    assert counters(inst) == (1, 0, 1, 0, 1), counters(inst)
    assert inst.scrapped_lots[0].cqt_scrapped


def test_detection_only_counts_but_completes():
    inst = run(build(65, rework=False))
    v, rw, sc, done, scr = counters(inst)
    assert (v, rw, sc, done, scr) == (1, 0, 0, 1, 0)


def test_skipped_entrance_step_opens_nothing():
    """StepPercent 0 on the entrance step: it is never performed, so no
    window opens and the (long) span to step 3 is not a violation."""
    inst = run(build(65, skip_entrance=True, rework=False))
    assert counters(inst) == (0, 0, 0, 1, 0), counters(inst)


def test_enforcement_off_is_bit_identical_to_detection_only():
    """A window-blind rule cannot see detection-only enforcement: every lot's
    completion time is identical with enforcement off and on."""
    a = run(build(65, enforce=False, n_lots=5, release_gap_min=30))
    b = run(build(65, enforce=True, rework=False, n_lots=5, release_gap_min=30))
    ta = sorted(l.done_at for l in a.done_lots)
    tb = sorted(l.done_at for l in b.done_lots)
    assert ta == tb and len(ta) == 5, (ta, tb)
    assert b.counter_cqt_violated == 5


def test_conservation():
    """releases = done + scrapped + WIP, on a run that scraps one lot."""
    inst = run(build(65, rework=True, max_rework=3, n_lots=3, release_gap_min=30), days=1)
    released = 4 - len(inst.dispatchable_lots)       # 3 lots + the sentinel
    assert released == len(inst.done_lots) + len(inst.scrapped_lots) + len(inst.active_lots)


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
