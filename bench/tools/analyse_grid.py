#!/usr/bin/env python3
"""Rules-breakdown grid: rule x window scale x load, per seed and aggregated.

Reads bench/results/grid/<rule>_x<scale>_L<load>_s<seed>_w<days>.json
(written by bench/tools/sweep_grid.sh).

Metrics, all window-scoped (after the shared day-90 qt(b) warm-up):
  good/d   shipped lots per day               -- the headline
  share    scrapped / (shipped + scrapped)    -- lost material
  CT       cycle time of SHIPPED lots, days   -- survivor-biased: scrap empties
           the fab, so CT falls as windows tighten. Never read it alone.
  slope    WIP slope over the final third of the window, lots/day
  cons     (shipped + scrapped + dWIP) / starts in window -- conservation
           check; far from 1.0 is a counter/window bug, not a result
Viable = slope < +5/day on EVERY seed AND max scrap share <= --share-max.
Verdict BREAKS = not viable; lagging = viable but good/d > 5 % below the best
rule at that (scale, load, window). On-time is not reported: scrapped lots
are never late.

    analyse_grid.py [--dir DIR] [--share-max 0.15] [--json out.json]
"""
import argparse
import glob
import json
import os
import re
import statistics as st
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
PAT = re.compile(r'(?P<rule>.+)_x(?P<scale>\d+)_L(?P<load>\d+)_s(?P<seed>\d+)_w(?P<win>\d+)\.json$')
NAN = float('nan')


def slope_per_day(samples):
    live = [s for s in samples if not s.get('warmup') and s.get('wip') is not None]
    if len(live) < 12:
        return None
    tail = live[len(live) * 2 // 3:]
    xs = [s['t'] / 86400.0 for s in tail]
    ys = [s['wip'] for s in tail]
    n = len(tail)
    mx, my = sum(xs) / n, sum(ys) / n
    den = sum((x - mx) ** 2 for x in xs)
    return sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / den if den else None


def cell(path, rule):
    d = json.load(open(path))
    win = d['days'] - (d.get('warmup_days') or 0)
    rows = [r for r in d['rows'] if r['rule'] == rule] or d['rows']
    r = rows[0]
    if r.get('interrupted'):
        return None
    cq = r.get('cqt') or {}
    shipped, scrapped = r['throughput'], cq.get('scrapped', 0)
    samples = r.get('samples') or []
    # 'starts' on a sample is a trailing-day count sampled hourly: mean = starts/day
    live = [s['starts'] for s in samples if not s.get('warmup') and s.get('starts') is not None]
    starts = sum(live) / len(live) * win if live else 0
    dwip = (r.get('wip_last') or 0) - (r.get('wip_first') or 0)
    return {
        'good_d': shipped / win, 'scrap_d': scrapped / win,
        'share': scrapped / max(1, shipped + scrapped),
        'ct': r.get('cycle_time_days'), 'slope': slope_per_day(samples),
        'viol': cq.get('violations'), 'util': r.get('util_pct'),
        'cons': (shipped + scrapped + dwip) / starts if starts else None,
        'wall_h': (r.get('wall_s') or 0) / 3600,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--dir', default=os.path.join(HERE, '..', 'results', 'grid'))
    ap.add_argument('--share-max', type=float, default=0.15)
    ap.add_argument('--json')
    a = ap.parse_args()

    groups = defaultdict(list)
    for p in sorted(glob.glob(os.path.join(a.dir, '*.json'))):
        m = PAT.search(os.path.basename(p))
        if not m:
            continue
        try:
            c = cell(p, m['rule'])
        except (KeyError, ValueError, TypeError, json.JSONDecodeError):
            c = None
        if c:
            c['seed'] = int(m['seed'])
            groups[(int(m['scale']), int(m['load']), int(m['win']), m['rule'])].append(c)

    best = defaultdict(float)
    for (sc, ld, w, _), cs in groups.items():
        best[(sc, ld, w)] = max(best[(sc, ld, w)], st.mean(c['good_d'] for c in cs))

    print(f'viable = final-third WIP slope < +5/day on every seed AND scrap share <= {a.share_max:.0%}')
    print('CT is over shipped lots only (survivor-biased). cons = (shipped+scrapped+dWIP)/starts.\n')
    print(f'{"scale":>5} {"load":>5} {"win":>4} {"rule":<10} {"n":>2} {"good/d":>7} {"vs best":>8} '
          f'{"share":>6} {"share min-max":>15} {"CT d":>6} {"max slope":>9} {"cons":>5} {"verdict":>8}')
    out, last = [], None
    for key in sorted(groups, key=lambda k: (-k[0], k[1], k[2], k[3])):
        sc, ld, w, rule = key
        cs = groups[key]
        if last and last != key[:3]:
            print()
        last = key[:3]
        good = st.mean(c['good_d'] for c in cs)
        shares = [c['share'] for c in cs]
        slopes = [c['slope'] for c in cs if c['slope'] is not None]
        mxs = max(slopes) if slopes else None
        cons = [c['cons'] for c in cs if c['cons']]
        cts = [c['ct'] for c in cs if c['ct'] is not None]
        viable = len(slopes) == len(cs) and mxs < 5 and max(shares) <= a.share_max
        gap = good / best[key[:3]] - 1 if best[key[:3]] else 0.0
        verdict = ('viable' if gap > -0.05 else 'lagging') if viable else 'BREAKS'
        print(f'{sc:>5} {ld / 100:>5.2f} {w:>4} {rule:<10} {len(cs):>2} {good:>7.1f} {gap:>+8.1%} '
              f'{st.mean(shares):>6.1%} {min(shares):>7.1%}-{max(shares):>6.1%} '
              f'{(st.mean(cts) if cts else NAN):>6.1f} {(mxs if mxs is not None else NAN):>+9.1f} '
              f'{(st.mean(cons) if cons else NAN):>5.2f} {verdict:>8}')
        out.append({'scale': sc, 'load': ld / 100, 'window_days': w, 'rule': rule,
                    'seeds': sorted(c['seed'] for c in cs), 'good_per_day': good,
                    'vs_best': gap, 'share_mean': st.mean(shares), 'share_max': max(shares),
                    'ct_mean': st.mean(cts) if cts else None, 'slope_max': mxs,
                    'verdict': verdict, 'cells': cs})
    if a.json:
        with open(a.json, 'w') as f:
            json.dump(out, f, indent=1)


if __name__ == '__main__':
    main()
