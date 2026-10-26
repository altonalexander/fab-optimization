#!/usr/bin/env python3
"""Is a grid cell's WIP slope a climb or post-warm-up settling?

For each cell: WIP at window start, mean WIP and OLS slope (lots/day) in each
third of the window, and the slope over the last 30 days. Settling looks like
thirds' slopes shrinking toward 0 (or turning over); a genuine climb keeps a
steady or growing slope. Also prints 10-day mean WIP so the shape is visible.

    grid_wip_trace.py FILE.json [FILE.json ...]
"""
import json
import os
import sys


def ols(pts):
    n = len(pts)
    if n < 12:
        return float('nan')
    mx = sum(x for x, _ in pts) / n
    my = sum(y for _, y in pts) / n
    den = sum((x - mx) ** 2 for x, _ in pts)
    return sum((x - mx) * (y - my) for x, y in pts) / den if den else float('nan')


print(f'{"cell":<24} {"wip0":>5} {"mean1/3":>8} {"mean2/3":>8} {"mean3/3":>8} '
      f'{"sl1/3":>6} {"sl2/3":>6} {"sl3/3":>6} {"last30":>6}  10-day means')
for path in sys.argv[1:]:
    d = json.load(open(path))
    r = d['rows'][0]
    pts = [(s['t'] / 86400.0, s['wip']) for s in r.get('samples') or []
           if not s.get('warmup') and s.get('wip') is not None]
    if not pts:
        continue
    k = len(pts) // 3
    thirds = [pts[:k], pts[k:2 * k], pts[2 * k:]]
    t_end = pts[-1][0]
    last30 = [p for p in pts if p[0] >= t_end - 30]
    t0 = pts[0][0]
    tens = []
    for i in range(0, int(t_end - t0) + 1, 10):
        seg = [y for x, y in pts if t0 + i <= x < t0 + i + 10]
        if seg:
            tens.append(round(sum(seg) / len(seg)))
    name = os.path.basename(path).replace('_L100', '').replace('.json', '')
    print(f'{name:<24} {r.get("wip_first", pts[0][1]):>5} '
          + ' '.join(f'{sum(y for _, y in t) / len(t):>8.0f}' for t in thirds) + ' '
          + ' '.join(f'{ols(t):>+6.1f}' for t in thirds)
          + f' {ols(last30):>+6.1f}  ' + ' '.join(str(v) for v in tens))
