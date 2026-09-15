#!/usr/bin/env python3
"""Consolidate every result file the paper cites into one dataset.

Reads bench/results/cliff/*.json (compare.py output) and writes
docs/paper/build/paper_data.json with, per run: the window-scoped KPIs, the
per-part split, the WIP/on-time/throughput sample series, and the
admissibility numbers (final-third WIP slope, conservation rate).

Every number in the paper's tables and figures comes from this file, so the
paper can be regenerated from the raw results and nothing is typed by hand.
"""
import glob
import json
import os

REPO = '/home/alton/src/fab-optimization/.claude/worktrees/adr-0013-runs'
R = os.path.join(REPO, 'bench', 'results', 'cliff')
OUT = os.path.join(REPO, 'docs', 'paper', 'build', 'paper_data.json')
DAY = 86400.0

# name -> (file, human label, group)
RUNS = {
    # the three-seed viability result (qt-warmed fab, 180-day window, 1.00x)
    'fifo_s0': ('qw_1.00_fifo.json', 'FIFO', 'seed0'),
    'cr_s0':   ('qw_1.00_cr.json',   'CR',   'seed0'),
    'qt_s0':   ('qw_1.00_qt.json',   'QT',   'seed0'),
    'fifo_s1': ('qw_s1_fifo.json',   'FIFO', 'seed1'),
    'cr_s1':   ('qw_s1_cr.json',     'CR',   'seed1'),
    'qt_s1':   ('qw_s1_qt.json',     'QT',   'seed1'),
    'fifo_s2': ('qw_s2_fifo.json',   'FIFO', 'seed2'),
    'cr_s2':   ('qw_s2_cr.json',     'CR',   'seed2'),
    'qt_s2':   ('qw_s2_qt.json',     'QT',   'seed2'),
    # baselines, tuned
    'qt50':    ('qt50_1.00.json', 'QT, promote < 50% window', 'baseline'),
    'qt25':    ('qt25_1.00.json', 'QT, promote < 25% window', 'baseline'),
    # the solver, three configurations
    'slate_inert_a': ('sl_1.00_slate_a.json', 'SLATE, inert q-time term', 'solver'),
    'slate_inert_b': ('sl_1.00_slate_b.json', 'SLATE, inert q-time term', 'solver'),
    'slate_fixed_a': ('sl2_1.00_slate_norm.json', 'SLATE, window-relative, untuned fallback', 'solver'),
    'slate_fixed_b': ('sl2_1.00_slate_norm_b.json', 'SLATE, window-relative, untuned fallback', 'solver'),
    'slate_sym':     ('sl3_1.00_slate_qt50.json', 'SLATE, window-relative, tuned fallback', 'solver'),
    # --- the solid version: five seeds of sort keys, three seeds x three
    #     replicates of the symmetric solver ---------------------------------
    'fifo_s3': ('qw_s3_fifo.json', 'FIFO', 'seed3'),
    'cr_s3':   ('qw_s3_cr.json',   'CR',   'seed3'),
    'qt_s3':   ('qw_s3_qt.json',   'QT',   'seed3'),
    'fifo_s4': ('qw_s4_fifo.json', 'FIFO', 'seed4'),
    'cr_s4':   ('qw_s4_cr.json',   'CR',   'seed4'),
    'qt_s4':   ('qw_s4_qt.json',   'QT',   'seed4'),
    'qt50_s0': ('qt50_1.00.json',  'QT tuned', 'baseline'),
    'qt50_s0_repeat': ('qt50_s0_repeat.json', 'QT tuned (repeat)', 'baseline'),
    'qt50_s1': ('qt50_s1.json', 'QT tuned', 'baseline'),
    'qt50_s2': ('qt50_s2.json', 'QT tuned', 'baseline'),
    'qt50_s3': ('qt50_s3.json', 'QT tuned', 'baseline'),
    'qt50_s4': ('qt50_s4.json', 'QT tuned', 'baseline'),
    'slate_s0_a': ('sl3_1.00_slate_qt50.json', 'SLATE sym', 'solver_sym'),
    'slate_s0_b': ('mx3_s0_slate_b.json', 'SLATE sym', 'solver_sym'),
    'slate_s0_c': ('mx3_s0_slate_c.json', 'SLATE sym', 'solver_sym'),
    'slate_s1_a': ('mx3_s1_slate_a.json', 'SLATE sym', 'solver_sym'),
    'slate_s1_b': ('mx3_s1_slate_b.json', 'SLATE sym', 'solver_sym'),
    'slate_s1_c': ('mx3_s1_slate_c.json', 'SLATE sym', 'solver_sym'),
    'slate_s2_a': ('mx3_s2_slate_a.json', 'SLATE sym', 'solver_sym'),
    'slate_s2_b': ('mx3_s2_slate_b.json', 'SLATE sym', 'solver_sym'),
    'slate_s2_c': ('mx3_s2_slate_c.json', 'SLATE sym', 'solver_sym'),
    # seed-0 coverage-split probe (20 days, first counters, no histogram)
    'probe_cov_s0': ('probe_cov_s0.json', 'SLATE sym, 20-day probe', 'probe'),
    # mechanism checks, 40 cold days, seed 0
    'dx_nocqt':  ('dx_nocqt.json',  'no enforcement', 'mechanism'),
    'dx_s8norw': ('dx_s8-norw.json', 'scale 8, detection only', 'mechanism'),
    'dx_s8rw':   ('dx_s8-rw.json',   'scale 8, rework, uncapped', 'mechanism'),
    'dx_s4rw':   ('dx_s4-rw.json',   'scale 4, rework, uncapped', 'mechanism'),
    'cap_s4':    ('cap_s4.json',     'scale 4, rework, cap 3', 'mechanism'),
    'cap_s8':    ('cap_s8.json',     'scale 8, rework, cap 3', 'mechanism'),
}


def series(row):
    s = [x for x in (row.get('samples') or [])
         if not x.get('warmup') and x.get('wip') is not None]
    return [{'day': x['t'] / DAY, 'wip': x['wip'], 'otd': x.get('otd'),
             'thr': x.get('thr'), 'ct': x.get('ct'), 'util': x.get('util')}
            for x in s]


def summarise(d, row):
    win = float(d['days']) - float(d.get('warmup_days') or 0)
    s = series(row)
    cq = row.get('cqt') or {}
    out = {
        'dataset': d['dataset'], 'seed': d['seed'], 'days': d['days'],
        'warmup_days': d.get('warmup_days'), 'window_days': win,
        'rule': row['rule'], 'fingerprint': row.get('fingerprint'),
        'wall_s': row.get('wall_s'),
        'good': row.get('throughput'),
        'good_per_day': (row.get('throughput') or 0) / win,
        'on_time_pct': row.get('on_time_pct'),
        'cycle_time_days': row.get('cycle_time_days'),
        'tardiness_lot_days': row.get('tardiness_lot_days'),
        'util_pct': row.get('util_pct'),
        'scanner_util_pct': row.get('scanner_util_pct'),
        'cqt_enforced': cq.get('enforced'), 'cqt_scale': cq.get('scale'),
        'violations': cq.get('violations'), 'reworks': cq.get('reworks'),
        'scrapped': cq.get('scrapped'), 'rework_enabled': cq.get('rework_enabled'),
        'violations_per_day': (cq.get('violations') or 0) / win,
        'scrap_per_day': (cq.get('scrapped') or 0) / win,
        'by_part': row.get('by_part') or {},
        'coverage': (row.get('detail') or {}).get('coverage'),
        'effective_coverage': (row.get('detail') or {}).get('effective_coverage'),
        'decisions': (row.get('detail') or {}).get('decisions'),
        'decisions_forced': (row.get('detail') or {}).get('decisions_forced'),
        'decisions_choice': (row.get('detail') or {}).get('decisions_choice'),
        'decisions_choice_covered': (row.get('detail') or {}).get('decisions_choice_covered'),
        'candidate_hist': (row.get('detail') or {}).get('candidate_hist'),
        'series': s,
    }
    if s:
        out['wip_first'] = s[0]['wip']
        out['wip_last'] = s[-1]['wip']
        out['wip_mean'] = sum(x['wip'] for x in s) / len(s)
        n = len(s) // 3
        t = s[2 * n:]
        out['wip_slope_final_third'] = ((t[-1]['wip'] - t[0]['wip'])
                                        / max(t[-1]['day'] - t[0]['day'], 1e-9))
        out['release_rate'] = ((row.get('throughput') or 0)
                               + (cq.get('scrapped') or 0)
                               + (s[-1]['wip'] - s[0]['wip'])) / win
    parts = out['by_part']
    if parts:
        v = [p['on_time_pct'] for p in parts.values()]
        out['part_spread'] = max(v) - min(v)
        out['punctual_per_day'] = sum(
            p['throughput'] * p['on_time_pct'] / 100 for p in parts.values()) / win
    return out


data = {}
for key, (f, label, group) in RUNS.items():
    p = f if f.startswith('/') else os.path.join(R, f)
    if not os.path.exists(p):
        print('MISSING', key, f)
        continue
    d = json.load(open(p))
    row = d['rows'][0]
    data[key] = summarise(d, row)
    data[key]['label'] = label
    data[key]['group'] = group
    data[key]['file'] = f

os.makedirs(os.path.dirname(OUT), exist_ok=True)
json.dump(data, open(OUT, 'w'))
print(f'wrote {OUT}: {len(data)} runs')
print()
print('%-16s %6s %8s %7s %9s %7s %7s %7s %8s' % (
    'run', 'good/d', 'on-time', 'CT', 'tard', 'scr/d', 'viol/d', 'slope', 'rel/d'))
for k, v in data.items():
    if 'wip_first' not in v:
        continue
    print('%-16s %6.1f %7.2f%% %7.1f %9.1f %7.1f %7.1f %+7.2f %8.1f' % (
        k, v['good_per_day'], v['on_time_pct'], v['cycle_time_days'],
        v['tardiness_lot_days'], v['scrap_per_day'], v['violations_per_day'],
        v['wip_slope_final_third'], v['release_rate']))
