"""
compare -- head-to-head, one environment, one generator, one horizon.

This is the table docs/adr/0002 asks for:

    rule    | cycle time | throughput | on-time % | tardiness
    --------+------------+------------+-----------+----------
    fifo    |            |            |           |
    cr      |            |            |           |
    slate   |            |            |           |

Every rule runs on the same dataset, the same seed and the same number of days,
so the only thing that differs between rows is the dispatching decision. That
is the entire point; anything that breaks it makes the table meaningless.

Rules:
    fifo, cr, lifo, random     the upstream sort keys
    slate                      CP-SAT slate, full pressure
    slate:none|due|full        the pressure ablation ladder (adr/0009)
    slate-cr                   HARNESS VALIDATION -- routes through the slate
                               call path but returns CR's ordering. Must
                               reproduce `cr` exactly. If it does not, the
                               plumbing is wrong and no other row is meaningful.

Usage:
    python3 bench/tools/compare.py --days 30 --rules cr,slate
    python3 bench/tools/compare.py --days 2 --rules cr,slate-cr   # validate
"""
import argparse
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import sim_runner  # noqa: E402  (bootstraps sys.path and cwd for the baseline)
from sim_runner import REPO, RESET_AT, SECONDS_PER_DAY  # noqa: E402

import slate_rule  # noqa: E402


def kpis(instance, warm_from):
    """Cycle time, throughput, on-time % and tardiness from finished lots.

    Computed here rather than via the baseline's print_statistics because that
    function pickles debug data and prints; this needs comparable numbers in a
    dict. The definitions match it: a lot counts once it is done, cycle time is
    wall time from release to completion, and `warm_from` drops the fill-up
    transient so the numbers describe steady state.
    """
    ct, on_time, tard, n = [], 0, 0.0, 0
    for lot in instance.done_lots:
        if lot.release_at < warm_from:
            continue
        if lot.done_at is None:
            continue
        n += 1
        ct.append((lot.done_at - lot.release_at) / SECONDS_PER_DAY)
        late = lot.done_at - lot.deadline_at
        if late <= 0:
            on_time += 1
        else:
            tard += late / SECONDS_PER_DAY
    return {
        'throughput': n,
        'cycle_time_days': round(sum(ct) / len(ct), 4) if ct else 0.0,
        'on_time_pct': round(100.0 * on_time / n, 2) if n else 0.0,
        'tardiness_lot_days': round(tard, 2),
    }


class _Fingerprint:
    """Rolling hash of the dispatch sequence.

    after_dispatch(instance, machine, dispatched) is called once per decision
    point with the instance in a consistent state, so (time, machine, whether
    it took work) is a faithful signature of what the rule chose. Two runs with
    the same fingerprint made the same decisions in the same order.
    """

    def __init__(self):
        import hashlib
        self._h = hashlib.blake2b(digest_size=8)
        self.n = 0

    def __call__(self, instance, machine, dispatched):
        self.n += 1
        self._h.update(f'{instance.current_time:.4f}|{machine.idx}|'
                       f'{int(dispatched)}\n'.encode())

    def hexdigest(self):
        return self._h.hexdigest()


def make_rule(spec, instance, args):
    """Turn a --rules token into something sim_runner.run accepts."""
    if spec == 'slate-cr':
        return slate_rule.CrPassthrough()
    if spec == 'slate' or spec.startswith('slate:'):
        pressure = spec.split(':', 1)[1] if ':' in spec else 'full'
        return slate_rule.SlateRule(
            instance, solver=args.solver, cycle_s=args.cycle,
            budget_s=args.budget, pressure=pressure, threads=args.threads,
            lazy=not args.no_lazy)
    return spec          # a plain name; sim_runner resolves it


def run_one(spec, args):
    from events import ResetEvent

    instance, run_to = sim_runner.build(
        args.dataset, args.days, args.seed, [], args.batch_strat)

    # Match greedy.py: after one simulated year the counters are zeroed so the
    # numbers describe steady state. Only meaningful past 365 days.
    use_reset = args.days > 365
    if use_reset:
        instance.add_event(ResetEvent(RESET_AT))
    warm_from = RESET_AT if use_reset else 0

    rule = make_rule(spec, instance, args)
    banner = rule.banner() if hasattr(rule, 'banner') else f'  rule: {spec}'
    print(f'\n=== {spec} ===', flush=True)
    print(banner, flush=True)

    # The slate is rebuilt on the planning cadence, NOT per decision point --
    # that is production timing, and it is what turns stale-slate degradation
    # into a measured quantity rather than an assumption (adr/0002).
    before = None
    if hasattr(rule, 'maybe_rebuild'):
        before = lambda inst: rule.maybe_rebuild(inst)   # noqa: E731

    # Behavioural fingerprint of the whole run: every dispatch decision, in
    # order. KPIs are a weak equality check -- on a short horizon no lot has
    # finished yet and every rule scores 0, which would let a broken harness
    # "validate". The decision sequence differs on the FIRST divergent choice.
    fp = _Fingerprint()

    t0 = time.time()
    interrupted = sim_runner.run(instance, run_to, rule,
                                 before_dispatch=before,
                                 after_dispatch=fp, stream=sys.stdout)
    wall = time.time() - t0
    if not interrupted:
        instance.finalize()

    row = {'rule': spec, 'wall_s': round(wall, 1),
           'decisions': fp.n, 'fingerprint': fp.hexdigest()}
    row.update(kpis(instance, warm_from))
    if hasattr(rule, 'stats'):
        row['detail'] = rule.stats()
    row['interrupted'] = interrupted
    print(f"  {row['throughput']} lots, "
          f"CT {row['cycle_time_days']}d, "
          f"on-time {row['on_time_pct']}%, "
          f"wall {row['wall_s']}s", flush=True)
    return row


def table(rows):
    w = max((len(r['rule']) for r in rows), default=6)
    head = (f"  {'rule':<{w}}  {'cycle time':>11}  {'throughput':>10}  "
            f"{'on-time %':>9}  {'tardiness':>11}  {'coverage':>8}")
    out = ['', head, '  ' + '-' * (len(head) - 2)]
    for r in rows:
        cov = r.get('detail', {}).get('coverage')
        cov_s = f'{cov*100:.1f}%' if isinstance(cov, float) else '-'
        out.append(f"  {r['rule']:<{w}}  {r['cycle_time_days']:>11.3f}  "
                   f"{r['throughput']:>10}  {r['on_time_pct']:>9.2f}  "
                   f"{r['tardiness_lot_days']:>11.1f}  {cov_s:>8}")
    return '\n'.join(out)


def check_validation(rows):
    """slate-cr must reproduce cr exactly. Say so loudly either way."""
    by = {r['rule']: r for r in rows}
    if 'cr' not in by or 'slate-cr' not in by:
        return None
    a, b = by['cr'], by['slate-cr']
    # Fingerprint first: it is the strict check. KPIs can agree trivially on a
    # short horizon where nothing has finished, so they are reported but are
    # not what the verdict rests on.
    keys = ('throughput', 'cycle_time_days', 'on_time_pct', 'tardiness_lot_days')
    diffs = [k for k in keys if a[k] != b[k]]
    if a['fingerprint'] != b['fingerprint']:
        return ('  VALIDATION FAIL: slate-cr made different decisions from cr '
                f"({a['decisions']} vs {b['decisions']} dispatches, "
                f"fp {a['fingerprint']} vs {b['fingerprint']}).\n"
                '                   The plumbing is wrong; no other row means '
                'anything until this passes.')
    if diffs:
        return ('  VALIDATION FAIL: identical decisions but KPIs differ on ' +
                ', '.join(diffs) + '.\n                   That should be '
                'impossible -- suspect the KPI code, not the rule.')
    return (f"  VALIDATION PASS: slate-cr reproduced cr's {a['decisions']} "
            'dispatch decisions exactly\n                   (fp '
            f"{a['fingerprint']}). The harness does not change the answer.")


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sim_runner.add_common_args(p, days_default=30)
    p.add_argument('--rules', default='fifo,cr,slate',
                   help='comma-separated (default: fifo,cr,slate)')
    p.add_argument('--solver', default='cpsat', choices=['cpsat', 'greedy'])
    p.add_argument('--cycle', type=float, default=60.0,
                   help='slate rebuild cadence, SIMULATED seconds')
    p.add_argument('--budget', type=float, default=0.005,
                   help='per-family solve budget, seconds. Measured: 5ms is '
                        '3.3x faster than 50ms with slightly BETTER coverage -- '
                        'a per-family model is small enough that CP-SAT closes '
                        'it well inside 5ms, and the rest of the budget goes on '
                        'proving optimality nobody collects.')
    p.add_argument('--threads', type=int, default=1)
    p.add_argument('--no-lazy', action='store_true',
                   help='re-solve every family every cycle')
    p.add_argument('--out', default=None, help='write JSON here')
    a = p.parse_args()
    a.dataset = sim_runner.normalize_dataset(a.dataset)
    a.dispatcher = None   # unused; --rules drives this tool

    print(f'  {a.dataset}  {a.days} days  seed={a.seed}  batch={a.batch_strat}')
    if a.days <= 365:
        print('  NOTE: <=365 days, so no warm-up reset. Numbers include the '
              'fill-up transient\n        and are not comparable to published '
              '730-day results.')

    rows = [run_one(spec.strip(), a) for spec in a.rules.split(',') if spec.strip()]

    print(table(rows))
    verdict = check_validation(rows)
    if verdict:
        print('\n' + verdict)

    payload = {
        'dataset': a.dataset, 'days': a.days, 'seed': a.seed,
        'batch_strat': a.batch_strat, 'solver': a.solver,
        'cycle_s': a.cycle, 'budget_s': a.budget,
        'rows': rows,
    }
    out = a.out or os.path.join(
        REPO, 'bench', 'results',
        f'compare_{a.dataset}_seed{a.seed}_{a.days}d.json')
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, 'w') as f:
        json.dump(payload, f, indent=2)
    print(f'\n  wrote {out}')


if __name__ == '__main__':
    main()
