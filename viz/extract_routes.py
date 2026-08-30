"""Aggregate SMT2020 routes to process areas and emit JSON for the route diagram.

Each route step names a process area in the final column of route_*.txt. Collapsing
the step sequence to that column turns a 500-step list into a readable flow while
preserving the re-entrancy that defines a wafer fab.
"""
import json, os, sys
from collections import Counter

DS = sys.argv[1] if len(sys.argv) > 1 else 'SMT2020_LVHM'
ROOT = os.path.join(os.path.dirname(__file__), '..', 'data', 'smt2020', DS)


def read_route(path):
    with open(path) as f:
        header = f.readline().rstrip('\n').split('\t')
    idx = {n: i for i, n in enumerate(header)}
    rows = []
    with open(path) as f:
        next(f)
        for line in f:
            if not line.strip():
                continue
            c = line.rstrip('\n').split('\t')
            c += [''] * (len(header) - len(c))
            rows.append({
                'step': int(c[idx['STEP']]),
                'desc': c[idx['DESC']],
                'fam': c[idx['STNFAM']],
                # final column holds the process area
                'area': c[-1].strip(),
                'sample': float(c[idx['StepPercent']]) if c[idx['StepPercent']] else 100.0,
                'rework': float(c[idx['REWORK']]) if c[idx['REWORK']] else 0.0,
                'rwkstep': int(c[idx['RWKSTEP']]) if c[idx['RWKSTEP']] else None,
                'batch': c[idx['PTPER']] == 'per_batch',
            })
    return rows


routes = {}
for fn in sorted(os.listdir(ROOT)):
    if not fn.startswith('route_') or not fn.endswith('.txt'):
        continue
    rid = fn[len('route_'):-len('.txt')]
    rows = read_route(os.path.join(ROOT, fn))
    seq = [r['area'] for r in rows]

    # collapse consecutive same-area steps into one "visit" to that bay
    visits = []
    for r in rows:
        if visits and visits[-1]['area'] == r['area']:
            visits[-1]['steps'] += 1
            visits[-1]['last'] = r['step']
        else:
            visits.append({'area': r['area'], 'steps': 1,
                           'first': r['step'], 'last': r['step']})

    trans = Counter()
    va = [v['area'] for v in visits]
    for a, b in zip(va, va[1:]):
        trans[(a, b)] += 1

    routes[rid] = {
        'id': 'r_' + rid,
        'n_steps': len(rows),
        'n_visits': len(visits),
        'areas': dict(Counter(seq)),
        'area_visits': dict(Counter(va)),
        'visits': visits,
        'seq': seq,
        'transitions': [{'from': a, 'to': b, 'n': n} for (a, b), n in trans.most_common()],
        'sampled': sum(1 for r in rows if r['sample'] < 100),
        'rework': [{'at': r['step'], 'back_to': r['rwkstep'], 'pct': r['rework'],
                    'area': r['area']} for r in rows if r['rework'] > 0],
        'batch_steps': sum(1 for r in rows if r['batch']),
    }

areas = sorted({a for r in routes.values() for a in r['areas']},
               key=lambda a: -sum(r['areas'].get(a, 0) for r in routes.values()))
print(json.dumps({'dataset': DS, 'areas': areas, 'routes': routes}))
