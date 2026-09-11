#!/usr/bin/env python3
"""gen_reticles -- write a reticle library beside the testbed (ADR 0014 §3.2).

    python3 bench/tools/gen_reticles.py --name reticles-1x --copies 1

Produces `data/smt2020/overlays/<name>/reticles.tsv`, and leaves any
`qualification.tsv` already in that directory alone -- the two constraint
classes are independent on purpose (ADR 0014 §3.1), so each can be switched
on by itself and its contribution read separately.

A reticle is per (part, photo layer), and the layer is the step's `DESC`
rather than its order, so a lot sent back by rework returns to the same
physical mask. Only the scanners hold one: station group `Litho` minus the
`LithoTrack_*` coat/develop families.

**Copies are the knob.** One mask per layer is the hard case: no two lots of
the same part and layer can expose at the same time anywhere in the fab, no
matter how many scanners are idle. Real fabs buy a second mask for the
layers that would otherwise choke, which is what `--extra-copies-top` does
for the highest-volume parts.

**The capacity check refuses, as in ADR 0013 §3.2.** A mask is a resource
with `copies x 24h` of capacity per day. If demand for one exceeds that, the
fab cannot make the part at all and every rule fails together -- a capacity
experiment wearing a scheduling experiment's clothes. The threshold is
deliberately looser than the tool one (0.95, not 0.90): high mask contention
is the regime being bought here, it is just infeasibility that is refused.
"""
import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import gen_overlay as go                                    # noqa: E402
import reticles as ret_mod                                  # noqa: E402

SECONDS_PER_DAY = 86400.0


def scanner_families(instance, groups_by_family):
    """The families that hold a mask, by station group (ADR 0014 §2)."""
    out = set()
    for fam in instance.family_machines:
        g = groups_by_family.get(fam)
        if g is not None and ret_mod.is_scanner_family(fam, g):
            out.add(fam)
    return out


def step_tool_seconds(step, cascading, lu):
    """Tool-seconds one lot occupies at `step`. Same model as gen_overlay."""
    fam = step.family
    t = step.cascading_time.m if hasattr(step.cascading_time, 'm') else 0.0
    t = float(t)
    if not cascading.get(fam, False):
        t += lu.get(fam, 0.0)
    if step.batch_max and step.batch_max > 1:
        t /= float(step.batch_max)
    return t * float(step.sampling_percent) / 100.0


def build(args):
    instance, _ = go.sim_runner.build(args.dataset, args.rate_horizon_days,
                                      args.seed, [], args.batch_strat)
    from read import read_all
    files = read_all('datasets/' + args.dataset)
    groups_by_family = {d['STNFAM']: d['STNGRP'] for d in files['tool.txt.1l']}

    fams = scanner_families(instance, groups_by_family)
    if not fams:
        raise SystemExit('  no scanner families found; nothing to do')

    cascading = {f: all(m.cascading for m in ms)
                 for f, ms in instance.family_machines.items()}
    lu = {f: sum(m.load_time + m.unload_time for m in ms) / max(1, len(ms))
          for f, ms in instance.family_machines.items()}

    rate = go.release_rate(instance, args.starts_scale)
    routes = go._routes_by_part(instance)

    # ---- one reticle per (part, layer) at a scanner ---------------------
    demand_s = {}                     # (part, layer) -> tool seconds / day
    for part, route in routes.items():
        for step in route.steps:
            if step.family not in fams:
                continue
            key = (part, step.step_name)
            demand_s[key] = demand_s.get(key, 0.0) + (
                step_tool_seconds(step, cascading, lu) * rate.get(part, 0.0))

    if not demand_s:
        raise SystemExit('  no scanner steps in any route; nothing to do')

    # ---- copies --------------------------------------------------------
    # Highest-volume parts get the extra mask, which is what a fab buys.
    by_part_demand = {}
    for (part, _layer), d in demand_s.items():
        by_part_demand[part] = by_part_demand.get(part, 0.0) + d
    hot = set(sorted(by_part_demand, key=by_part_demand.get,
                     reverse=True)[:args.extra_copies_top])

    table, util = {}, {}
    for (part, layer), d in sorted(demand_s.items()):
        copies = args.copies + (1 if part in hot else 0)
        if args.autosize:
            need = d / (args.max_util * SECONDS_PER_DAY)
            copies = max(copies, int(-(-need // 1)))       # ceil
        rid = f'R_{part}_{layer}'.replace(' ', '_')
        table[(part, layer)] = (rid, copies)
        util[rid] = d / (copies * SECONDS_PER_DAY)

    over = {r: u for r, u in util.items() if u > args.max_util}
    hottest = sorted(util.items(), key=lambda kv: -kv[1])[:8]

    print(f'\n  reticles {args.name}  copies={args.copies}'
          f'  extra-copies-top={args.extra_copies_top}'
          f'  transport={args.transport_s:g}s')
    print(f'  {len(table)} (part, layer) pairs on {len(fams)} scanner '
          f'families, {sum(c for _, c in table.values())} physical masks')
    print('\n  busiest masks (share of a mask-day at '
          f'{args.starts_scale:g}x starts):')
    for r, u in hottest:
        print(f'    {u * 100:6.1f}%  {r}')

    if over:
        print(f'\n  REFUSED: {len(over)} reticle(s) above '
              f'{args.max_util * 100:g}% of their own capacity:')
        for r, u in sorted(over.items(), key=lambda kv: -kv[1])[:8]:
            print(f'    {u * 100:6.1f}%  {r}')
        print('\n  Nothing written. Raise --copies or --extra-copies-top, or\n'
              '  pass --autosize to let the generator buy the masks it needs.\n'
              '  A mask over its own capacity means the fab cannot make that\n'
              '  part at all, which is a capacity result, not a scheduling one.')
        return None

    lib = ret_mod.Reticles(table, args.transport_s, fams, provenance={})
    d = os.path.join(args.root or ret_mod_overlay_dir(), args.name)
    lib.write_dir(d)

    prov_path = os.path.join(d, 'provenance.json')
    prov = {}
    if os.path.isfile(prov_path):
        with open(prov_path) as f:
            prov = json.load(f)
    prov['reticles'] = {
        'generator': 'bench/tools/gen_reticles.py',
        'generator_version': 1,
        'dataset': args.dataset,
        'copies': args.copies,
        'extra_copies_top': args.extra_copies_top,
        'autosize': bool(args.autosize),
        'transport_s': args.transport_s,
        'max_util': args.max_util,
        'starts_scale_checked': args.starts_scale,
        'scanner_families': sorted(fams),
        'pairs': len(table),
        'masks': sum(c for _, c in table.values()),
        'reticle_hash': lib.hash,
        'busiest': {r: round(u, 4) for r, u in hottest},
        'rework_modelled': False,
    }
    with open(prov_path, 'w') as f:
        json.dump(prov, f, indent=2, sort_keys=True)
        f.write('\n')
    print(f'\n  wrote {os.path.relpath(d, go.REPO)}  '
          f'reticle hash {lib.hash}')
    return lib


def ret_mod_overlay_dir():
    import overlay
    return overlay.OVERLAY_DIR


def main():
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--dataset', default='SMT2020_LVHM')
    p.add_argument('--seed', type=int, default=0)
    p.add_argument('--batch-strat', default='Demand',
                   choices=['Max', 'Min', 'RoundRobin', 'Demand'])
    p.add_argument('--name', required=True,
                   help='overlay directory to write reticles.tsv into')
    p.add_argument('--copies', type=int, default=1,
                   help='physical masks per (part, layer); 1 is the hard case')
    p.add_argument('--extra-copies-top', type=int, default=0,
                   metavar='N', help='give the N highest-volume parts one '
                                     'extra mask per layer')
    p.add_argument('--autosize', action='store_true',
                   help='buy however many masks keep every reticle under '
                        '--max-util instead of refusing')
    p.add_argument('--transport-s', type=float, default=900.0,
                   help='seconds to move a mask between scanners (default 900)')
    p.add_argument('--max-util', type=float, default=0.95)
    p.add_argument('--starts-scale', type=float, default=1.03)
    p.add_argument('--rate-horizon-days', type=float, default=120.0)
    p.add_argument('--root', default=None)
    a = p.parse_args()
    if build(a) is None:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
