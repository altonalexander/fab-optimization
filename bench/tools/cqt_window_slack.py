#!/usr/bin/env python3
"""How much room does each published CQT window leave?

For every window, compare its length with the MINIMUM time a lot needs to get
from entrance completion to exit start with zero queueing: the processing of
the intervening steps plus the dataset's mean transport (7.5 min per move).
slack = window / touch.  slack < 1 is infeasible for any dispatcher; slack
near 1 means the lot must wait for essentially nothing at any intervening or
exit tool.
"""
import collections
import csv
import glob
import os

D = os.path.join(os.path.dirname(__file__), '..', '..', 'baselines', 'pyscfabsim', 'datasets', 'SMT2020_LVHM')
U = {'min': 1, 'hr': 60, 'sec': 1 / 60, 's': 1 / 60, '': 1}

tools = collections.Counter()
for r in csv.DictReader(open(os.path.join(D, 'tool.txt.1l')), delimiter='\t'):
    tools[r['STNFAM']] += int(float(r['STNQTY'] or 1))

rows, rwk = [], collections.Counter()
for f in sorted(glob.glob(os.path.join(D, 'route_*.txt'))):
    S = list(csv.DictReader(open(f), delimiter='\t'))
    idx = {int(s['STEP']): i for i, s in enumerate(S)}
    for i, s in enumerate(S):
        if s['REWORK'] or s['RWKSTEP']:
            rwk[(s['STEP_CQT'] != '')] += 1
        if not s['STEP_CQT']:
            continue
        j = idx[int(s['STEP_CQT'])]
        win = float(s['CQT']) * U[s['CQTUNITS']]
        touch = 7.5 * (j - i)
        for k in range(i + 1, j):
            t = S[k]
            p = float(t['PTIME']) * U[t['PTUNITS']]
            touch += p * 25 if t['PTPER'] == 'per_piece' else p
        ex = S[j]
        rows.append((win, touch, j - i - 1, s['DESC'], ex['DESC'], ex['STNFAM'], tools.get(ex['STNFAM'], 0)))

print(len(rows), 'windows')
sl = sorted(w / t for w, t, *_ in rows)
for q in (0, .1, .25, .5, .75, .9, 1):
    print(f'slack q{q:<4}: {sl[int(q * (len(sl) - 1))]:.2f}')
print('share slack<2:', f'{sum(x < 2 for x in sl) / len(sl):.0%}', ' <5:', f'{sum(x < 5 for x in sl) / len(sl):.0%}')
print('window lengths (hr):', collections.Counter(round(r[0] / 60, 1) for r in rows).most_common(8))
print('intervening steps:', collections.Counter(r[2] for r in rows).most_common(6))
print('exit families (fam, tools):', collections.Counter((r[5], r[6]) for r in rows).most_common(8))
print('rework-flagged steps (on a CQT entrance?):', dict(rwk))
print('tightest:')
for w, t, n, a, b, fam, nt in sorted(rows, key=lambda r: r[0] / r[1])[:6]:
    print(f'  win {w / 60:.2f}h touch {t / 60:.2f}h  {a} -> {b} ({fam}, {nt} tools)')
