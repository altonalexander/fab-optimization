#!/usr/bin/env python3
"""gen_trim -- size the tool set to a target utilisation (ADR 0015 §3.2).

    python3 bench/tools/gen_trim.py --name trim-82 --target 0.82

Writes `data/smt2020/overlays/<name>/trim.tsv`: one row per family with the
tool count that puts its STATIC load at or below `--target` at the reference
start rate. Families already above the target are left alone -- this only
removes slack, it never adds tools, so the fab's true constraint keeps its
capacity and the knee is not manufactured by starving it.

The demand model is the one ADR 0013's generator uses and the one the run
executes: `cascading_time` is what occupies the tool, load/unload is added
unless the family cascades, a batch step is charged 1/batch_max per lot, and
`sampling_percent` is the share of lots that visit the step at all. Rework is
NOT modelled, so real demand is a few percent above this -- which makes the
trim slightly conservative, the right direction for a floor.

Floors, each because the obvious version deletes something that matters:

  * **Batch families keep enough tools to form a batch.** A furnace family
    trimmed to 1 tool still works, but a family whose steps batch 6 lots and
    which is the only route to them becomes a serialisation point that has
    nothing to do with the experiment.
  * **Never below 1**, and never below `--min-tools`.
  * **`Delay` is never touched**: 400 pseudo-stations for fixed waits
    (ADR 0008), not capacity.
  * **Families with no measurable demand are left alone** rather than trimmed
    to the floor; a family the routes never visit is not evidence of slack.
"""
import argparse
import json
import math
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import gen_overlay as go                                     # noqa: E402
import trim as trim_mod                                      # noqa: E402

SECONDS_PER_DAY = 86400.0


def family_load(instance, starts_scale):
    """(family -> (tools, static load, demand tool-seconds/day))."""
    per_lot = go.machine_seconds_per_lot(instance)            # (fam, part) -> s
    rate = go.release_rate(instance, starts_scale)            # part -> lots/day
    demand = {}
    for (fam, part), s in per_lot.items():
        demand[fam] = demand.get(fam, 0.0) + s * rate.get(part, 0.0)
    out = {}
    for fam, machines in instance.family_machines.items():
        n = len(machines)
        d = demand.get(fam, 0.0)
        out[fam] = (n, d / (n * SECONDS_PER_DAY) if n else 0.0, d)
    return out


def batch_floor(instance, fam):
    """Tools a batch family needs to keep forming batches: 1 is enough to run
    a batch, but we keep 2 where the family has 2 so a single breakdown does
    not serialise the whole product mix through one furnace."""
    biggest = 1
    for m in instance.family_machines.get(fam, ()):
        for step in getattr(m, 'steps', ()) or ():
            biggest = max(biggest, int(getattr(step, 'batch_max', 1) or 1))
    return 2 if biggest > 1 else 1


def build(args):
    instance, _ = go.sim_runner.build(args.dataset, args.rate_horizon_days,
                                      args.seed, [], args.batch_strat)
    load = family_load(instance, args.starts_scale)

    # --set is the explicit form and it may raise a count as well as lower it.
    # Sizing from STATIC load is what broke the first trim: static omits
    # rework, PM and breakdowns and understates real load by ~11%, so an 82%
    # static target landed at ~91% measured and the fab collapsed. Counts
    # derived from a MEASURED run are passed in here instead, which is also
    # the only way to ADD capacity -- the automatic path below only ever
    # removes slack, so it can never move a bottleneck off a family.
    if args.set:
        table = {}
        for fam, (n, _u, _d) in load.items():
            if not fam.startswith(trim_mod.NEVER_TRIM_PREFIX):
                table[fam] = n
        for spec in args.set:
            fam, _, val = spec.partition('=')
            if fam not in table:
                raise SystemExit(f'  --set {spec}: no family {fam!r}')
            table[fam] = int(val)
        delta = sum(table[f] - load[f][0] for f in table)
        t = trim_mod.Trim(args.name, table, provenance={'trim': {
            'generator': 'bench/tools/gen_trim.py --set',
            'generator_version': 1,
            'dataset': args.dataset,
            'explicit': list(args.set),
            'tools_before': sum(load[f][0] for f in table),
            'tools_after': sum(table.values()),
            'tools_delta': delta,
            'sized_from': 'measured run utilisation, not static load',
        }})
        print(f'\n  trim {args.name} (explicit)')
        for spec in args.set:
            fam = spec.split('=')[0]
            print(f'    {fam:<22} {load[fam][0]:3d} -> {table[fam]:3d}')
        print(f'  total {sum(load[f][0] for f in table)} -> '
              f'{sum(table.values())} ({delta:+d})')
        d = t.write(args.root)
        print(f'  wrote {os.path.relpath(d, go.REPO)}  trim hash {t.hash}')
        return t

    table, notes = {}, []
    kept = removed = 0
    for fam, (n, u, d) in sorted(load.items()):
        if fam.startswith(trim_mod.NEVER_TRIM_PREFIX):
            continue
        if d <= 0.0:
            notes.append(f'{fam}: no measured demand, left at {n}')
            table[fam] = n
            kept += n
            continue
        if u >= args.target:
            table[fam] = n
            kept += n
            continue
        # tools needed for static load to reach the target
        want = math.ceil(d / (args.target * SECONDS_PER_DAY))
        floor = max(args.min_tools, batch_floor(instance, fam))
        new = max(floor, min(n, want))
        table[fam] = new
        kept += new
        removed += n - new

    total_before = sum(n for fam, (n, _u, _d) in load.items()
                       if not fam.startswith(trim_mod.NEVER_TRIM_PREFIX))
    # resulting static load per family
    after = {}
    for fam, (n, u, d) in load.items():
        if fam in table and table[fam] > 0 and d > 0:
            after[fam] = d / (table[fam] * SECONDS_PER_DAY)

    # Delay is excluded from BOTH sides. It is 400 pseudo-stations for fixed
    # waits (ADR 0008); counting its demand over the real tool count inflates
    # the fab-wide figure, which is how an 83% reading came out of a 72% fab.
    dem_tot = sum(d for f, (_n, _u, d) in load.items()
                  if not f.startswith(trim_mod.NEVER_TRIM_PREFIX))
    fabwide_after = dem_tot / (kept * SECONDS_PER_DAY) if kept else 0.0
    fabwide_before = dem_tot / (total_before * SECONDS_PER_DAY) if total_before else 0.0

    print(f'\n  trim {args.name}  target={args.target:.0%}  '
          f'reference starts={args.starts_scale:g}x')
    print(f'  {total_before} process tools -> {kept}  '
          f'({removed} removed, {removed / max(1, total_before):.0%})')
    print(f'  fab-wide static load {fabwide_before:.1%} -> {fabwide_after:.1%}')

    bands = [(0.90, 1.01, '>=90%'), (0.85, 0.90, '85-90%'),
             (0.80, 0.85, '80-85%'), (0.70, 0.80, '70-80%'),
             (0.50, 0.70, '50-70%'), (0.0, 0.50, '<50%')]
    print('\n  load distribution after trim:')
    for lo, hi, lab in bands:
        fams = [f for f, u in after.items() if lo <= u < hi]
        tl = sum(table[f] for f in fams)
        print(f'    {lab:8s} {len(fams):3d} families  {tl:4d} tools')

    print('\n  busiest 10 after trim:')
    for f, u in sorted(after.items(), key=lambda kv: -kv[1])[:10]:
        n0 = load[f][0]
        print(f'    {u * 100:5.1f}%  {f:22s} {n0:3d} -> {table[f]:3d}')

    print('\n  biggest reductions:')
    cuts = sorted(((load[f][0] - table[f], f) for f in table),
                  reverse=True)[:10]
    for cut, f in cuts:
        if cut <= 0:
            break
        print(f'    -{cut:3d}  {f:22s} {load[f][0]:3d} -> {table[f]:3d}  '
              f'({load[f][1] * 100:.0f}% -> {after.get(f, 0) * 100:.0f}%)')

    t = trim_mod.Trim(args.name, table, provenance={
        'trim': {
            'generator': 'bench/tools/gen_trim.py',
            'generator_version': 1,
            'dataset': args.dataset,
            'target': args.target,
            'min_tools': args.min_tools,
            'starts_scale_reference': args.starts_scale,
            'tools_before': total_before,
            'tools_after': kept,
            'tools_removed': removed,
            'fabwide_static_load_before': round(fabwide_before, 4),
            'fabwide_static_load_after': round(fabwide_after, 4),
            'static_load_after': {f: round(u, 4) for f, u in sorted(after.items())},
            'rework_modelled': False,
            'notes': notes,
        }
    })
    d = t.write(args.root)
    print(f'\n  wrote {os.path.relpath(d, go.REPO)}  trim hash {t.hash}')
    return t


def main():
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--dataset', default='SMT2020_LVHM')
    p.add_argument('--seed', type=int, default=0)
    p.add_argument('--batch-strat', default='Demand',
                   choices=['Max', 'Min', 'RoundRobin', 'Demand'])
    p.add_argument('--name', required=True)
    p.add_argument('--set', action='append', default=None,
                   metavar='FAMILY=N',
                   help='set one family explicitly, up or down (repeatable). '
                        'Bypasses --target; use counts derived from MEASURED '
                        'utilisation, not static load (adr/0015 §2).')
    p.add_argument('--target', type=float, default=0.82,
                   help='static load to size each family to (default 0.82)')
    p.add_argument('--min-tools', type=int, default=2,
                   help='never trim a family below this (default 2)')
    p.add_argument('--starts-scale', type=float, default=1.0,
                   help='start rate the sizing is computed at (default 1.0)')
    p.add_argument('--rate-horizon-days', type=float, default=120.0)
    p.add_argument('--root', default=None)
    a = p.parse_args()
    build(a)


if __name__ == '__main__':
    main()
