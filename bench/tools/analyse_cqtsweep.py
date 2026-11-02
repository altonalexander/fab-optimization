import glob
import json
import os
import re

D = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'results', 'cqtsweep90')


def slope_per_day(samples):
    """Least-squares WIP slope, lots/day, over the FINAL THIRD of the
    reporting window. Samples are hourly."""
    live = [s for s in samples if not s.get('warmup') and s.get('wip') is not None]
    if len(live) < 12:
        return None
    tail = live[len(live) * 2 // 3:]
    n = len(tail)
    xs = [i / 24.0 for i in range(n)]          # days
    ys = [s['wip'] for s in tail]
    mx = sum(xs) / n
    my = sum(ys) / n
    num = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    den = sum((x - mx) ** 2 for x in xs)
    return num / den if den else None


cells = {}
for p in sorted(glob.glob(os.path.join(D, '*.json'))):
    m = re.match(r's(\d+)_x(\d+)\.json', os.path.basename(p))
    seed, scale = int(m.group(1)), int(m.group(2))
    d = json.load(open(p))
    for r in d['rows']:
        cells[(scale, seed, r['rule'])] = {
            'tp': r['throughput'],
            'ct': r['cycle_time_days'],
            'otd': r['on_time_pct'],
            'tard': r['tardiness_lot_days'],
            'scrap': (r.get('cqt') or {}).get('scrapped', 0),
            'viol': (r.get('cqt') or {}).get('violations', 0),
            'rew': (r.get('cqt') or {}).get('reworks', 0),
            'wip0': r.get('wip_first'), 'wip1': r.get('wip_last'),
            'slope': slope_per_day(r.get('samples') or []),
            'util': r.get('util_pct'),
        }

SCALES = [10, 5, 3, 2, 1]
SEEDS = [0, 1, 2, 3, 4]

for rule in ('qt', 'cr'):
    print(f'\n===== {rule} =====')
    print(f'{"scale":>5} {"seed":>4} {"lots":>6} {"CT d":>7} {"on-time":>8} '
          f'{"tard":>9} {"scrap":>7} {"viol":>8} {"WIP0":>6} {"WIP1":>6} '
          f'{"slope/d":>8} {"util":>6}')
    for sc in SCALES:
        for sd in SEEDS:
            c = cells.get((sc, sd, rule))
            if not c:
                print(f'{sc:>5} {sd:>4}   (missing)')
                continue
            sl = '   n/a' if c['slope'] is None else f'{c["slope"]:+8.1f}'
            print(f'{sc:>5} {sd:>4} {c["tp"]:>6} {c["ct"]:>7.2f} '
                  f'{c["otd"]:>7.2f}% {c["tard"]:>9.1f} {c["scrap"]:>7} '
                  f'{c["viol"]:>8} {str(c["wip0"]):>6} {str(c["wip1"]):>6} '
                  f'{sl} {c["util"]:>6.1f}')
        print()

# viability: WIP slope near zero and scrap bounded, on EVERY seed
print('\n===== viability by scale (qt) =====')
print(f'{"scale":>5} {"max|slope|/d":>13} {"max scrap":>10} {"min on-time":>12} '
      f'{"mean lots/d":>12}  verdict')
for sc in SCALES:
    rows = [cells.get((sc, sd, 'qt')) for sd in SEEDS]
    if any(r is None for r in rows):
        print(f'{sc:>5}   incomplete')
        continue
    sl = [abs(r['slope']) for r in rows if r['slope'] is not None]
    mx_sl = max(sl) if sl else float('nan')
    mx_scrap = max(r['scrap'] for r in rows)
    mn_otd = min(r['otd'] for r in rows)
    lots_d = sum(r['tp'] for r in rows) / len(rows) / 90.0
    verdict = 'VIABLE' if (mx_sl < 5 and mx_scrap == 0) else (
        'marginal' if mx_sl < 10 else 'DIVERGING')
    print(f'{sc:>5} {mx_sl:>13.1f} {mx_scrap:>10} {mn_otd:>11.2f}% '
          f'{lots_d:>12.1f}  {verdict}')

print('\n===== qt vs cr, per scale (mean over seeds) =====')
print(f'{"scale":>5} {"qt on-time":>11} {"cr on-time":>11} {"gap":>7} '
      f'{"qt scrap":>9} {"cr scrap":>9} {"qt lots":>8} {"cr lots":>8}')
for sc in SCALES:
    q = [cells.get((sc, sd, 'qt')) for sd in SEEDS]
    c = [cells.get((sc, sd, 'cr')) for sd in SEEDS]
    if any(x is None for x in q + c):
        continue
    qo = sum(x['otd'] for x in q) / 5
    co = sum(x['otd'] for x in c) / 5
    print(f'{sc:>5} {qo:>10.2f}% {co:>10.2f}% {qo - co:>+7.2f} '
          f'{sum(x["scrap"] for x in q) / 5:>9.0f} '
          f'{sum(x["scrap"] for x in c) / 5:>9.0f} '
          f'{sum(x["tp"] for x in q) / 5:>8.0f} '
          f'{sum(x["tp"] for x in c) / 5:>8.0f}')
