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

Warm-up:
    --warmup-days N resumes every rule from the SAME simulator checkpoint at
    day N, built once under --warmup-dispatcher (fifo). The rules then diverge
    from an identical fab -- same WIP, same tool setups, same pending
    breakdowns, same RNG -- which is what makes the rows an A/B. It is also
    the checkpoint bench/tools/sim_feed.py streams the dashboard from, so a
    row here and a run on the Results tab describe the same experiment.
    Without it every rule simulates from day 0 and the numbers include the
    fill-up transient.

    KPIs are over the reporting window: lots that COMPLETED after day N,
    with cycle time, on-time % and tardiness of those lots. That matches the
    feed's trailing-day definition and is what a 30-day window can measure;
    counting only lots released after day N would leave a 30-day window
    nearly empty against a ~22-day cycle time.

Usage:
    python3 bench/tools/compare.py --days 120 --warmup-days 90 --rules cr,slate
    python3 bench/tools/compare.py --days 92 --warmup-days 90 --rules cr,slate-cr
    python3 bench/tools/compare.py --merge a.json b.json --out all.json
"""
import argparse
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
# sim_runner chdirs into the vendored simulator on import, so a relative
# --out or --merge path has to be resolved against where the user ran from.
ORIG_CWD = os.getcwd()

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
        if lot.done_at is None or lot.done_at < warm_from:
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


def make_sampler(rule_name, warm_from):
    """Hourly KPI series, taken by the FEED'S OWN plugin.

    The dashboard's Results page lays a benchmark row over the live run, and
    the run store summarises both. That is only honest if one piece of code
    measures both: the first version of this harness kept its own sampler,
    which counted completions cumulatively where the feed counts a trailing
    day, and WIP as waiting lots where the feed counts waiting + running --
    so the Results tab showed `slate` at "14 lots/day" beside a live run at
    56, from the same simulator. Now sim_feed.FeedPlugin rides this run with a
    null sink and no store, and its `_kpi_sample` is the definition, full
    stop: WIP, running, util, trailing-day throughput / cycle time / on-time /
    tardiness, decisions and how many the optimizer made, starts, and the
    lot-hour split. `warmup` marks rows before `warm_from`.
    """
    import sim_feed
    # The plugin polls the dashboard's playback control file for pacing; a
    # benchmark must never block on a "paused" left by the live feed.
    sim_feed.CONTROL_FILE = os.path.join(HERE, 'no-such-control-file.json')

    class _Sampler(sim_feed.FeedPlugin):
        def __init__(self):
            super().__init__(sim_feed.FileSink(os.devnull, False), 0.0)
            self.rows = []
            self.rule = rule_name
            self.store = None

        def _kpi_sample(self, instance, t):
            row = super()._kpi_sample(instance, t)
            self.rows.append(dict(row, warmup=t < warm_from))
            return row

    return _Sampler()


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


def warm_checkpoint(args):
    """The shared warm-up checkpoint for this run, building it if missing.

    Delegates to sim_feed.py, which owns the checkpoint format, so the fab a
    benchmark row starts from is byte-for-byte the one the dashboard feed
    streams from.
    """
    import sim_feed
    ck = sim_feed.find_ckpt(args.dataset, args.seed, args.warmup_dispatcher,
                            args.warmup_days, args.batch_strat, args.days)
    if ck is None:
        print(f'  no {args.warmup_dispatcher} checkpoint for day '
              f'{args.warmup_days:g} (horizon >= {args.days}d); building it',
              flush=True)
        import subprocess
        cmd = [sys.executable, os.path.join(HERE, 'sim_feed.py'),
               '--dataset', args.dataset, '--seed', str(args.seed),
               '--batch-strat', args.batch_strat, '--days', str(args.days),
               '--dispatcher', args.warmup_dispatcher,
               '--warmup-days', str(args.warmup_days),
               '--checkpoint-only', '--no-store', '--speed', '0',
               '--out', os.devnull]
        env = dict(os.environ, SIM_CONTROL_FILE=os.devnull)
        rc = subprocess.call(cmd, cwd=REPO, env=env)
        ck = sim_feed.find_ckpt(args.dataset, args.seed, args.warmup_dispatcher,
                                args.warmup_days, args.batch_strat, args.days)
        if rc != 0 or ck is None:
            sys.exit('  could not build the warm-up checkpoint')
    return ck


def load_warm(args, sampler):
    """A simulator instance resumed at day --warmup-days, with `sampler`
    attached as its plugin and the checkpoint's per-lot books restored into
    it -- so the trailing-day KPIs are continuous across the warm-up line
    exactly as they are for the feed."""
    import sim_feed
    ck = warm_checkpoint(args)
    instance = sim_feed.load_checkpoint(ck, sampler)
    if instance is None:
        sys.exit(f'  checkpoint {ck} unreadable')
    print(f'  resumed {os.path.relpath(ck, REPO)} at day '
          f'{instance.current_time / SECONDS_PER_DAY:g}', flush=True)
    return instance, SECONDS_PER_DAY * args.days


def run_one(spec, args):
    from events import ResetEvent

    use_reset = args.days > 365
    warm_from = RESET_AT if use_reset else args.warmup_days * SECONDS_PER_DAY
    sampler = make_sampler(spec, warm_from)
    if args.warmup_days and not use_reset:
        instance, run_to = load_warm(args, sampler)
    else:
        instance, run_to = sim_runner.build(
            args.dataset, args.days, args.seed, [sampler], args.batch_strat)

    # Warm-up. Two schemes exist in this repo and they are not the same thing:
    #
    #   greedy.py's ResetEvent at one year zeroes the MACHINE counters, so
    #   utilisation describes steady state. Applied past 365 days to stay
    #   comparable with the baseline's published figures.
    #
    #   sim_feed's --warmup-days N instead discards the first N days from the
    #   REPORTED window, which is what makes a 120-day run describe the fab
    #   rather than the fill-up transient.
    #
    # Both are supported, because a row produced here has to be comparable with
    # a row produced by the feed -- and comparing a warmed run against an
    # unwarmed one is exactly the class of error this harness exists to stop.
    if use_reset:
        instance.add_event(ResetEvent(RESET_AT))

    rule = make_rule(spec, instance, args)
    banner = rule.banner() if hasattr(rule, 'banner') else f'  rule: {spec}'
    print(f'\n=== {spec} ===', flush=True)
    print(banner, flush=True)

    # The slate is rebuilt on the planning cadence, NOT per decision point --
    # that is production timing, and it is what turns stale-slate degradation
    # into a measured quantity rather than an assumption (adr/0002).
    def before(inst):
        if hasattr(rule, 'maybe_rebuild'):
            rule.maybe_rebuild(inst)

    # Behavioural fingerprint of the whole run: every dispatch decision, in
    # order. KPIs are a weak equality check -- on a short horizon no lot has
    # finished yet and every rule scores 0, which would let a broken harness
    # "validate". The decision sequence differs on the FIRST divergent choice.
    fp = _Fingerprint()

    def after(instance, machine, dispatched):
        fp(instance, machine, dispatched)

    t0 = time.time()
    interrupted = sim_runner.run(instance, run_to, rule,
                                 before_dispatch=before,
                                 after_dispatch=after, stream=sys.stdout)
    wall = time.time() - t0
    if not interrupted:
        instance.finalize()

    row = {'rule': spec, 'wall_s': round(wall, 1),
           'decisions': fp.n, 'fingerprint': fp.hexdigest()}
    row.update(kpis(instance, warm_from))
    if hasattr(rule, 'stats'):
        row['detail'] = rule.stats()
    row['interrupted'] = interrupted
    row['samples'] = sampler.rows
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


def merge(paths, out):
    """Rules run as separate processes (they are independent by construction)
    land in one file, and the gate is checked over the union."""
    base, rows = None, []
    keys = ('dataset', 'days', 'seed', 'batch_strat', 'warmup_days',
            'cycle_s', 'budget_s')
    for path in paths:
        with open(path) as f:
            d = json.load(f)
        if base is None:
            base = d
        else:
            bad = [k for k in keys if d.get(k) != base.get(k)]
            if bad:
                sys.exit(f'  {path} differs from {paths[0]} on '
                         + ', '.join(bad) + '; not comparable')
        rows.extend(d['rows'])
    seen = set()
    rows = [r for r in rows if not (r['rule'] in seen or seen.add(r['rule']))]
    print(f"  {base['dataset']}  {base['days']} days  seed={base['seed']}  "
          f"batch={base['batch_strat']}  warmup={base.get('warmup_days') or 0:g}d")
    print(table(rows))
    verdict = check_validation(rows)
    if verdict:
        print('\n' + verdict)
    if out:
        payload = dict(base, rows=rows)
        with open(out, 'w') as f:
            json.dump(payload, f, indent=2)
        print(f'\n  wrote {out}')


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
    p.add_argument('--warmup-days', type=float, default=0.0,
                   help='discard lots released before this day from the KPIs. '
                        'Match the feed (--warmup-days 90 on a 120-day run) or '
                        'the rows are not comparable.')
    p.add_argument('--warmup-dispatcher', default='fifo',
                   help='rule that runs the shared warm-up (default: fifo)')
    p.add_argument('--merge', nargs='+', metavar='JSON',
                   help='combine per-rule result files (same dataset/seed/'
                        'days/warm-up) into one table; runs nothing')
    p.add_argument('--out', default=None, help='write JSON here')
    a = p.parse_args()
    a.dataset = sim_runner.normalize_dataset(a.dataset)
    a.dispatcher = None   # unused; --rules drives this tool

    if a.out:
        a.out = os.path.join(ORIG_CWD, a.out)
    if a.merge:
        merge([os.path.join(ORIG_CWD, m) for m in a.merge], a.out)
        return

    print(f'  {a.dataset}  {a.days} days  seed={a.seed}  batch={a.batch_strat}'
          + (f'  warmup={a.warmup_days:g}d' if a.warmup_days else ''))
    if a.days <= 365 and not a.warmup_days:
        print('  NOTE: <=365 days and no --warmup-days, so the numbers include '
              'the fill-up\n        transient and are not comparable to warmed '
              'runs or to published\n        730-day results.')

    rows = [run_one(spec.strip(), a) for spec in a.rules.split(',') if spec.strip()]

    print(table(rows))
    verdict = check_validation(rows)
    if verdict:
        print('\n' + verdict)

    payload = {
        'dataset': a.dataset, 'days': a.days, 'seed': a.seed,
        'batch_strat': a.batch_strat, 'solver': a.solver,
        'warmup_days': a.warmup_days,
        'warmup_dispatcher': a.warmup_dispatcher if a.warmup_days else None,
        'cycle_s': a.cycle, 'budget_s': a.budget,
        'rows': rows,
    }
    out = a.out or os.path.join(
        REPO, 'bench', 'results',
        f'compare_{a.dataset}_seed{a.seed}_{a.days}d'
        + (f'_w{a.warmup_days:g}' if a.warmup_days else '') + '.json')
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, 'w') as f:
        json.dump(payload, f, indent=2)
    print(f'\n  wrote {out}')


if __name__ == '__main__':
    main()
