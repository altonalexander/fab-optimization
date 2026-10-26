"""Scrap band under scrap-on-first: scales 8/5/3/2/1 x promote threshold, all seeds.

Scales 5/3/2 come from bench/results/scrapfirst (three fracs); scales 8 and 1
from bench/results/cqtdiag (frac 0.50 only). Under scrap-on-first the fab can
be flow-stable and still lose most of its material, so stability (WIP slope)
and scrap share are reported separately rather than folded into one verdict.
"""
import glob
import json
import os
import re

from analyse_qt_retune import load

HERE = os.path.dirname(os.path.abspath(__file__))
SF = os.path.join(HERE, '..', 'results', 'scrapfirst')
DG = os.path.join(HERE, '..', 'results', 'cqtdiag')
DAYS = 90
SEEDS = [0, 1, 2, 3, 4]

cells = {}
for p in glob.glob(os.path.join(SF, '*.json')):
    m = re.match(r's(\d+)_x(\d+)_qp(\d+)\.json', os.path.basename(p))
    if m:
        cells[(int(m.group(2)), int(m.group(3)), int(m.group(1)))] = load(p)
for p in glob.glob(os.path.join(DG, 'scrapfirst_*.json')):
    m = re.match(r'scrapfirst_s(\d+)_x(\d+)\.json', os.path.basename(p))
    if m:
        cells[(int(m.group(2)), 50, int(m.group(1)))] = load(p)


def share(c):
    return c['scrap'] / max(1, c['tp'] + c['scrap'])


print('scrap-on-first, rule qt. share = scrapped / (shipped + scrapped) in the 90-day window.')
print('stable = WIP final-third slope < +5/day. on-time counts only lots that SHIPPED.\n')
print(f'{"scale":>5} {"frac":>5} {"stable":>7} {"lots/d":>7} {"scrap/d":>8} '
      f'{"share min-max":>15} {"on-time":>8} {"CT d":>6} {"max slope":>10}')
for scale in (8, 5, 3, 2, 1):
    for qp in sorted({q for (s, q, _) in cells if s == scale}):
        rows = [cells.get((scale, qp, sd)) for sd in SEEDS]
        if any(r is None for r in rows):
            print(f'{scale:>5} {qp/100:>5.2f}  (missing seeds)')
            continue
        st = sum(1 for r in rows if r['slope'] is not None and r['slope'] < 5)
        sh = [share(r) for r in rows]
        print(f'{scale:>5} {qp/100:>5.2f} {st:>5}/5 '
              f'{sum(r["tp"] for r in rows)/5/DAYS:>7.1f} '
              f'{sum(r["scrap"] for r in rows)/5/DAYS:>8.1f} '
              f'{min(sh):>6.1%} - {max(sh):>6.1%} '
              f'{sum(r["otd"] for r in rows)/5:>7.1f}% '
              f'{sum(r["ct"] for r in rows)/5:>6.1f} '
              f'{max(r["slope"] for r in rows if r["slope"] is not None):>10.1f}')
    print()
