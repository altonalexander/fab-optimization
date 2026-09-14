#!/usr/bin/env python3
"""Load `compare.py` result files into the Postgres run store.

`sim_feed.py` writes runs to Postgres as it streams; `compare.py` writes JSON
and nothing else, so a headless benchmark never reached the Results tab. That
is the gap this closes: the rows that settle a question should be the rows the
dashboard shows, not a second set produced another way.

One JSON file may hold several rules (`--rules fifo,cr,slate`), and each rule
becomes its own run, because a run is "one trajectory under one dispatcher"
and comparing rules is exactly what the tab is for.

    import_runs.py bench/results/cliff/qw_1.00_qt.json ...
    import_runs.py --label "qt, tuned" FILE          # override the notes
    import_runs.py --delete-existing FILE ...        # clear the store first

The KPI names match what sim_feed already writes, so both producers land in
the same vocabulary and a row from either is readable by the same UI.
"""
import argparse
import json
import os
import subprocess
import sys

SECONDS_PER_DAY = 86400.0

# metric name -> how to get it from a compare.py row
FAB_METRICS = {
    'cycle_time_days': lambda r, w: r.get('cycle_time_days'),
    'on_time_pct': lambda r, w: r.get('on_time_pct'),
    'throughput_day': lambda r, w: (r.get('throughput') or 0) / w,
    'tardiness_days': lambda r, w: r.get('tardiness_lot_days'),
    'util_pct': lambda r, w: r.get('util_pct'),
}


def psql(sql, args=(), container='fab-data-postgres'):
    """Run one statement. Values are passed as psql variables, never
    interpolated into the SQL, so a product name with a quote in it cannot
    become a syntax error or anything worse."""
    cmd = ['docker', 'exec', '-i', container, 'psql', '-U', 'fab', '-d', 'fab',
           '-t', '-A', '-v', 'ON_ERROR_STOP=1']
    for i, a in enumerate(args):
        cmd += ['-v', f'a{i}={a}']
    out = subprocess.run(cmd + ['-c', sql], capture_output=True, text=True)
    if out.returncode != 0:
        sys.exit(f'psql failed:\n{out.stderr.strip()}\n  sql: {sql[:200]}')
    return out.stdout.strip()


def q(s):
    """Quote a Python value as a SQL literal."""
    if s is None:
        return 'NULL'
    if isinstance(s, bool):
        return 'TRUE' if s else 'FALSE'
    if isinstance(s, (int, float)):
        return repr(s)
    return "'" + str(s).replace("'", "''") + "'"


def import_row(path, d, row, label, git_sha):
    win = float(d['days']) - float(d.get('warmup_days') or 0)
    rule = row['rule']
    cq = row.get('cqt') or {}
    note = label or (
        f"{rule}; compare.py; window {win:g}d"
        + (f"; q-time x{cq['scale']:g}" if cq.get('enforced') else '')
        + (f"; scrap cap {cq['max_rework']}" if cq.get('max_rework') else '')
        + f"; {os.path.basename(path)}")

    run_id = psql(
        'INSERT INTO runs (dataset, seed, dispatcher, batch_strat, days, '
        'warmup_days, git_sha, solver, solver_linked, notes, finished_at) '
        f'VALUES ({q(d["dataset"])}, {q(d["seed"])}, {q(rule)}, '
        f'{q(d.get("batch_strat", "Demand"))}, {q(d["days"])}, '
        f'{q(d.get("warmup_days"))}, {q(git_sha)}, {q(d.get("solver"))}, '
        f'TRUE, {q(note)}, now()) RETURNING id;').split('\n')[0].strip()
    # psql echoes the command tag ("INSERT 0 1") after the RETURNING value
    # even under -t -A, so take the first line or every later statement is
    # built around a run_id with a newline in it.

    vals = []
    for metric, fn in FAB_METRICS.items():
        v = fn(row, win)
        if v is not None:
            vals.append(f"({run_id}, {q(metric)}, '', {q(float(v))})")
    # per-product on-time and cycle time -- the split that showed the solver
    # rebalancing between products, and the reason this tab needs `product`
    for part, pv in (row.get('by_part') or {}).items():
        for metric, key in (('on_time_pct', 'on_time_pct'),
                            ('cycle_time_days', 'cycle_time_days'),
                            ('throughput_day', 'throughput')):
            v = pv.get(key)
            if v is None:
                continue
            if key == 'throughput':
                v = v / win
            vals.append(f"({run_id}, {q(metric)}, {q(part)}, {q(float(v))})")
    if vals:
        psql('INSERT INTO run_kpis (run_id, metric, product, value) VALUES '
             + ','.join(vals) + ' ON CONFLICT DO NOTHING;')

    samples = row.get('samples') or []
    cols = ('t', 'warmup', 'wip', 'running', 'util', 'thr', 'ct', 'otd',
            'tard', 'dec', 'opt', 'wq', 'wb', 'wp', 'starts')
    batch, n = [], 0
    for s in samples:
        if s.get('t') is None:
            continue
        batch.append('(' + str(run_id) + ',' +
                     ','.join(q(s.get(c)) for c in cols) + ')')
        if len(batch) >= 500:
            psql(f'INSERT INTO run_kpi_samples (run_id,{",".join(cols)}) '
                 'VALUES ' + ','.join(batch) + ' ON CONFLICT DO NOTHING;')
            n += len(batch)
            batch = []
    if batch:
        psql(f'INSERT INTO run_kpi_samples (run_id,{",".join(cols)}) VALUES '
             + ','.join(batch) + ' ON CONFLICT DO NOTHING;')
        n += len(batch)

    print(f'  run #{run_id:<3} {rule:<6} {win:g}d  '
          f'{len(vals)} kpis, {n} samples   {os.path.basename(path)}')
    return run_id


def main():
    p = argparse.ArgumentParser()
    p.add_argument('files', nargs='+')
    p.add_argument('--label', default=None,
                   help='notes text for every run in this call')
    p.add_argument('--delete-existing', action='store_true',
                   help='remove all existing runs first (CASCADE)')
    p.add_argument('--git-sha', default=None)
    a = p.parse_args()

    sha = a.git_sha or subprocess.run(
        ['git', 'rev-parse', '--short', 'HEAD'], capture_output=True,
        text=True).stdout.strip() or None

    if a.delete_existing:
        before = psql('SELECT count(*) FROM runs;')
        print(f'deleting {before} existing run(s) and their KPIs/samples...')
        psql('DELETE FROM runs;')

    print('importing:')
    for f in a.files:
        d = json.load(open(f))
        for row in d['rows']:
            import_row(f, d, row, a.label, sha)

    print('\nstore now holds:')
    print(psql("SELECT id || '  ' || dispatcher || '  ' || days || 'd  ' || "
               "coalesce(notes,'') FROM runs ORDER BY id;"))


main()
