import glob
import json
import os
import re

D = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'results', 'cqtretune')
D90 = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'results', 'cqtsweep90')


def slope_per_day(samples):
    live = [s for s in samples if not s.get('warmup') and s.get('wip') is not None]
    if len(live) < 12:
        return None
    tail = live[len(live) * 2 // 3:]
    n = len(tail)
    xs = [i / 24.0 for i in range(n)]
    ys = [s['wip'] for s in tail]
    mx, my = sum(xs) / n, sum(ys) / n
    den = sum((x - mx) ** 2 for x in xs)
    return (sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / den) if den else None


def load(path, rule_want='qt'):
    d = json.load(open(path))
    for r in d['rows']:
        if r['rule'] != rule_want:
            continue
        return {
            'tp': r['throughput'], 'ct': r['cycle_time_days'],
            'otd': r['on_time_pct'], 'tard': r['tardiness_lot_days'],
            'scrap': (r.get('cqt') or {}).get('scrapped', 0),
            'viol': (r.get('cqt') or {}).get('violations', 0),
            'wip1': r.get('wip_last'), 'slope': slope_per_day(r.get('samples') or []),
            'util': r.get('util_pct'),
        }
    return None


cells = {}
for p in sorted(glob.glob(os.path.join(D, '*.json'))):
    m = re.match(r's(\d+)_x(\d+)_qp(\d+)\.json', os.path.basename(p))
    if not m:
        continue
    seed, scale, qp = int(m.group(1)), int(m.group(2)), int(m.group(3))
    c = load(p)
    if c:
        cells[(scale, qp, seed)] = c

# untuned baseline from the first sweep (frac 1.0 -> qp100)
for p in sorted(glob.glob(os.path.join(D90, '*.json'))):
    m = re.match(r's(\d+)_x(\d+)\.json', os.path.basename(p))
    seed, scale = int(m.group(1)), int(m.group(2))
    c = load(p)
    if c:
        cells[(scale, 100, seed)] = c

SEEDS = [0, 1, 2, 3, 4]
print('qt by scale and promote threshold. viable = scrap<=50 and WIP slope < +5/day.\n')
for scale in (10, 8, 5, 3):
    qps = sorted({qp for (sc, qp, _s) in cells if sc == scale})
    if not qps:
        continue
    print(f'--- scale {scale} ---')
    print(f'{"frac":>6} {"seed":>4} {"lots":>6} {"lots/d":>7} {"on-time":>8} '
          f'{"tard":>9} {"scrap":>7} {"WIP end":>8} {"slope/d":>8}')
    for qp in qps:
        for sd in SEEDS:
            c = cells.get((scale, qp, sd))
            if not c:
                print(f'{qp/100:>6.2f} {sd:>4}    (missing)')
                continue
            sl = '  n/a' if c['slope'] is None else f'{c["slope"]:+8.1f}'
            print(f'{qp/100:>6.2f} {sd:>4} {c["tp"]:>6} {c["tp"]/90:>7.1f} '
                  f'{c["otd"]:>7.2f}% {c["tard"]:>9.1f} {c["scrap"]:>7} '
                  f'{str(c["wip1"]):>8} {sl}')
        print()

print('\n===== viability: all five seeds must hold =====')
print(f'{"scale":>5} {"frac":>6} {"seeds viable":>13} {"max scrap":>10} '
      f'{"max slope":>11} {"mean lots/d":>12} {"mean on-time":>13} '
      f'{"mean tard":>10}  verdict')
for scale in (10, 8, 5, 3):
    for qp in sorted({qp for (sc, qp, _s) in cells if sc == scale}):
        rows = [cells.get((scale, qp, sd)) for sd in SEEDS]
        if any(r is None for r in rows):
            continue
        # Viable = WIP not GROWING (a draining fab is not diverging, so the
        # test is one-sided) and scrap below 1% of the ~5,090 lots released in
        # the window. Exact-zero scrap rejected cells that lost one lot.
        ok = sum(1 for r in rows
                 if r['scrap'] <= 50 and r['slope'] is not None and r['slope'] < 5)
        mx_scrap = max(r['scrap'] for r in rows)
        mx_sl = max(r['slope'] for r in rows if r['slope'] is not None)
        verdict = ('VIABLE (all 5)' if ok == 5 else
                   f'BIFURCATES ({ok}/5)' if ok else 'COLLAPSES')
        print(f'{scale:>5} {qp/100:>6.2f} {ok:>12}/5 {mx_scrap:>10} '
              f'{mx_sl:>11.1f} {sum(r["tp"] for r in rows)/5/90:>12.1f} '
              f'{sum(r["otd"] for r in rows)/5:>12.2f}% '
              f'{sum(r["tard"] for r in rows)/5:>10.1f}  {verdict}')
