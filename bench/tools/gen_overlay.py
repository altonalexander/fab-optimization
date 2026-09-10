"""gen_overlay -- write a tool qualification matrix beside the testbed (ADR 0013 §3.2).

    python3 bench/tools/gen_overlay.py --name dedication-all-50 \
        --scope all --fraction 0.50 --overlay-seed 7

Produces `data/smt2020/overlays/<name>/{qualification.tsv,provenance.json}`.
Never touches `data/smt2020/SMT2020_*`: the pristine LVHM stays the baseline
row and the dataset symlink stays the one-load guarantee (ADR 0013 §2).

Three properties are the whole design, and each is here because the obvious
version of it turns a dispatching experiment into a capacity experiment:

**Balanced, not iid.** Independent coin flips per (tool, part) leave some part
with one qualified scanner at a bottleneck family by chance. Every rule then
collapses onto that tool and the row measures the matrix, not the rule. Parts
are dealt to the least-loaded tools of a shuffled family instead, so each part
holds the same share of the family's capacity and each tool carries the same
number of parts to within one.

**Floors.** At least two qualified tools per (family, part) wherever the family
has two, and every tool qualified for at least one part. Without these the
generator silently deletes capacity, which reads as a dispatching result.

**A capacity check that refuses.** Printed per family and enforced: with part
`p` qualified on tool set `M(p)`, any set of parts `S` must fit in the tools it
can reach, so the family's effective utilisation is

    max over S of  ( sum of demand(p) for p in S ) / ( |union of M(p)| x 24h )

-- Hall's condition read as a load. With ten parts that is 1023 subsets per
family, enumerated exactly. If any family exceeds `--max-util` at the operating
start rate the overlay is NOT written. ADR 0013 §4: drop the fraction rather
than the floors.

Demand comes from the simulator's own instance -- the future lots it has
already built, and its own `Step` objects for process, cascading, load/unload,
batching and metrology sampling -- rather than from a second reading of the
route files. A load model that exists twice will drift from the fab it claims
to describe (summary §4.6 is the same lesson about KPIs).

The overlay seed is SEPARATE from the simulation seed. The matrix is a property
of the dataset, identical for every rule and every seed of a comparison; if it
moved with the run seed, two rows would be two fabs.
"""
import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
ORIG_CWD = os.getcwd()

import sim_runner  # noqa: E402  (bootstraps sys.path and cwd for the baseline)
from sim_runner import REPO, SECONDS_PER_DAY  # noqa: E402

import overlay as overlay_mod  # noqa: E402
from overlay import Overlay, machine_name  # noqa: E402

GENERATOR_VERSION = 1

# The 400-station `Delay_32` family is not a toolset. ADR 0008 records it as
# how PySCFabSim models transport: one uniform draw for the whole fab, served
# by a pseudo-family. Qualifying parts on a subset of it would restrict a
# delay, which is neither a real fab's behaviour nor a matching problem, and
# at 400 machines it dominates every count in the capacity table.
PSEUDO_GROUPS = ('Delay_32',)

SCOPE_ALIASES = {
    'litho': ('Litho',),
    'implant': ('Implant',),
    'diffusion': ('Diffusion',),
    'etch': ('Dry_Etch', 'Wet_Etch'),
}


def families_in_scope(instance, groups_by_family, scope, include_delay):
    """Station families the matrix will constrain. `all` means every real one."""
    fams = sorted(instance.family_machines)
    if not include_delay:
        fams = [f for f in fams if groups_by_family.get(f) not in PSEUDO_GROUPS]
    if scope == 'all':
        return fams
    wanted = set()
    for token in scope.split(','):
        token = token.strip()
        if not token:
            continue
        wanted.update(SCOPE_ALIASES.get(token.lower(), (token,)))
    picked = [f for f in fams if groups_by_family.get(f) in wanted]
    if not picked:
        raise SystemExit(
            f'  --scope {scope!r} matched no station family. Groups present: '
            + ', '.join(sorted(set(groups_by_family.values()))))
    return picked


def machine_seconds_per_lot(instance):
    """Expected tool-seconds a lot of each part asks of each family.

    Read off the simulator's own Step objects so the arithmetic below is the
    same model the run will execute:

      * `cascading_time` is what occupies the TOOL (`Instance.get_times`);
        load and unload are added unless the tool cascades, and the family's
        cascading flag is taken from the tools themselves.
      * a batch step is shared, so its tool time is charged 1/batch_max per
        lot -- the optimistic end, which makes the capacity check the
        permissive one it should be for a refusal threshold.
      * `sampling_percent` is the share of lots that visit a metrology step
        at all.

    Rework loops are NOT modelled: a reworked lot repeats steps, so real
    demand is a few percent above this. Recorded in the provenance and in the
    printed table rather than silently absorbed.
    """
    cascading = {}
    for fam, machines in instance.family_machines.items():
        cascading[fam] = all(m.cascading for m in machines)
    lu = {}
    for fam, machines in instance.family_machines.items():
        lu[fam] = (sum(m.load_time + m.unload_time for m in machines)
                   / max(1, len(machines)))

    demand = {}          # (family, part) -> tool seconds per released lot
    for part, route in _routes_by_part(instance).items():
        for step in route.steps:
            fam = step.family
            t = step.cascading_time.m if hasattr(step.cascading_time, 'm') else 0.0
            t = float(t)
            if not cascading.get(fam, False):
                t += lu.get(fam, 0.0)
            if step.batch_max and step.batch_max > 1:
                t /= float(step.batch_max)
            t *= float(step.sampling_percent) / 100.0
            demand[(fam, part)] = demand.get((fam, part), 0.0) + t
    return demand


def _routes_by_part(instance):
    """part name -> Route. The lots carry the mapping; the routes dict is
    keyed by route id, and nothing else in the instance joins the two."""
    out = {}
    for lot in list(instance.dispatchable_lots) + list(instance.active_lots):
        if lot.part_name in out:
            continue
        steps = list(lot.processed_steps) + list(lot.remaining_steps)
        if steps:
            out[lot.part_name] = type('R', (), {'steps': steps})()
    return out


def release_rate(instance, starts_scale, lo_day=30.0, window_days=30.0):
    """Lots per day per part, from the lots the simulator has already built.

    `order.txt` releases on a constant interval, so any window inside the
    schedule gives the rate. The window starts at day 30, past the initial-WIP
    releases and comfortably inside the horizon the instance was built for --
    FileInstance only materialises lots up to `run_to`, so a short-horizon
    instance would report a rate of nearly zero and every family would clear
    the capacity check trivially.
    """
    lo = SECONDS_PER_DAY * lo_day
    hi = SECONDS_PER_DAY * (lo_day + window_days)
    n = {}
    for lot in instance.dispatchable_lots:
        if lo <= lot.release_at < hi:
            n[lot.part_name] = n.get(lot.part_name, 0) + 1
    if not n:
        raise SystemExit(
            f'  no releases between day {lo_day:g} and {lo_day + window_days:g}: '
            'the instance horizon is too short to read a start rate from')
    return {p: c / window_days * starts_scale for p, c in n.items()}


def deal(machines, parts, k, rng):
    """Qualify each part on `k` of `machines`, keeping both sides balanced.

    Parts are taken in a shuffled order and each is given the `k` tools
    carrying the fewest parts so far, ties broken by a shuffled tool order.
    That leaves every part with exactly `k` distinct tools and every tool with
    the same part count to within one -- which a cyclic deal does not, since
    part `p`'s tools land on `(p + jP) mod n` and collide whenever
    `gcd(P, n) > 1`.
    """
    order = list(machines)
    rng.shuffle(order)
    rank = {m: i for i, m in enumerate(order)}
    load = {m: 0 for m in machines}
    parts = list(parts)
    rng.shuffle(parts)

    out = {}
    for part in parts:
        chosen = sorted(machines, key=lambda m: (load[m], rank[m]))[:k]
        for m in chosen:
            load[m] += 1
        out[part] = set(chosen)

    # Floor: every tool qualified for at least one part. Only reachable when
    # parts x k < tools, i.e. a large family at a low fraction.
    orphans = [m for m in machines if load[m] == 0]
    for m in orphans:
        part = min(parts, key=lambda p: (len(out[p]), p))
        out[part].add(m)
        load[m] += 1
    return out, len(orphans)


def deal_skew(machines, parts, k, rng):
    """`--skew`: a deliberately hard matrix, off by default.

    Tools are weighted so early ones in the shuffled order attract parts and
    late ones are near-exclusive. The capacity check still governs, so this
    produces a matrix that is hard to MATCH rather than one short of capacity
    -- which is the distinction the balanced default exists to protect.
    """
    order = list(machines)
    rng.shuffle(order)
    weight = {m: 1.0 / (i + 1) for i, m in enumerate(order)}
    parts = list(parts)
    rng.shuffle(parts)
    out, load = {}, {m: 0 for m in machines}
    for part in parts:
        chosen = sorted(machines,
                        key=lambda m: (load[m] * weight[m], order.index(m)))[:k]
        for m in chosen:
            load[m] += 1
        out[part] = set(chosen)
    orphans = [m for m in machines if load[m] == 0]
    for m in orphans:
        out[min(parts, key=lambda p: (len(out[p]), p))].add(m)
    return out, len(orphans)


def effective_util(fam_demand_s, qualified, n_machines):
    """Hall's condition as a utilisation. See the module docstring.

    `fam_demand_s` is tool-seconds per day by part; `qualified` maps part to
    the set of tool ordinals it may use. Returns (utilisation, the worst
    subset of parts). A part absent from `qualified` may use every tool.
    """
    parts = sorted(fam_demand_s)
    if not parts or not n_machines:
        return 0.0, ()
    everything = frozenset(range(n_machines))
    reach = {p: frozenset(qualified.get(p, everything)) for p in parts}
    worst, worst_set = 0.0, ()
    for mask in range(1, 1 << len(parts)):
        sub = [parts[i] for i in range(len(parts)) if mask >> i & 1]
        tools = set()
        for p in sub:
            tools |= reach[p]
        if not tools:
            return float('inf'), tuple(sub)
        u = sum(fam_demand_s[p] for p in sub) / (len(tools) * SECONDS_PER_DAY)
        if u > worst:
            worst, worst_set = u, tuple(sub)
    return worst, worst_set


def build(args):
    # Built over a long horizon on purpose: FileInstance materialises every
    # future lot up to run_to, and release_rate() reads the start rate off
    # them. Construction only -- nothing is simulated here.
    instance, _ = sim_runner.build(args.dataset, args.rate_horizon_days,
                                   args.seed, [], args.batch_strat)

    from read import read_all
    files = read_all('datasets/' + args.dataset)
    groups_by_family = {d['STNFAM']: d['STNGRP'] for d in files['tool.txt.1l']}

    fams = families_in_scope(instance, groups_by_family, args.scope,
                             args.include_delay)
    per_lot = machine_seconds_per_lot(instance)
    rate = release_rate(instance, args.starts_scale)
    parts = sorted(rate)

    import random
    rng = random.Random(args.overlay_seed)

    table, notes = {}, []
    orphans_fixed = 0
    for fam in fams:
        n = len(instance.family_machines[fam])
        ordinals = list(range(n))
        if n < 2:
            notes.append(f'{fam}: 1 tool, left fully qualified (floor)')
            continue
        k = min(n, max(2, int(round(args.fraction * n))))
        if k >= n:
            notes.append(f'{fam}: {n} tools, fraction rounds to all, left fully qualified')
            continue
        dealer = deal_skew if args.skew else deal
        qual, orphans = dealer(ordinals, parts, k, rng)
        orphans_fixed += orphans
        for part, ords in qual.items():
            table[(fam, part)] = {machine_name(fam, o) for o in sorted(ords)}

    # ---- capacity check ------------------------------------------------
    rows, refused = [], []
    for fam in sorted(instance.family_machines):
        if groups_by_family.get(fam) in PSEUDO_GROUPS and not args.include_delay:
            continue
        n = len(instance.family_machines[fam])
        fam_demand = {p: per_lot.get((fam, p), 0.0) * rate.get(p, 0.0)
                      for p in parts}
        fam_demand = {p: v for p, v in fam_demand.items() if v > 0}
        if not fam_demand:
            continue
        pristine = sum(fam_demand.values()) / (n * SECONDS_PER_DAY)
        qualified = {}
        for p in fam_demand:
            names = table.get((fam, p))
            if names is not None:
                qualified[p] = {int(s.rsplit('#', 1)[1]) for s in names}
        eff, worst = effective_util(fam_demand, qualified, n)
        rows.append((fam, n, len(qualified) and min(len(v) for v in qualified.values()) or n,
                     pristine, eff, worst))
        # Refuse only what the MATRIX pushed over. Some LVHM families sit
        # above the threshold on the pristine fab already -- LithoMet_FE_19 is
        # at 90.7% with every tool qualified for every part -- and blaming an
        # overlay for the load the testbed shipped with would refuse even an
        # empty matrix. The level is a property of the fab and is printed;
        # the increase is the overlay's, and is what is enforced.
        if eff > args.max_util and eff > pristine + 1e-9:
            refused.append((fam, pristine, eff, worst))

    print(f'\n  overlay {args.name}  scope={args.scope}  fraction={args.fraction:g}'
          f'  seed={args.overlay_seed}  skew={bool(args.skew)}')
    print(f'  capacity check at {args.starts_scale:g}x starts '
          f'(rework not modelled; real demand is a few percent above this)\n')
    print(f'    {"family":<22} {"tools":>5} {"qual":>5} {"pristine":>9} {"effective":>10}   worst subset')
    print('    ' + '-' * 78)
    for fam, n, k, pristine, eff, worst in sorted(rows, key=lambda r: -r[4]):
        if eff > args.max_util and eff > pristine + 1e-9:
            flag = '  <-- OVER (overlay)'
        elif eff > args.max_util:
            flag = '  <-- over on the pristine fab already'
        else:
            flag = ''
        sub = ','.join(w.replace('part_', 'p') for w in worst) if len(worst) < len(parts) else 'all'
        print(f'    {fam:<22} {n:>5} {k:>5} {pristine*100:>8.1f}% {eff*100:>9.1f}%   {sub}{flag}')
    # Fab-wide load over REAL tools: the 400-station Delay pseudo-family is
    # not capacity, and counting it divides the same demand by 1,313 instead
    # of 913. The number to check this model against is not the fab-wide one
    # -- it reads 55% where ADR 0012 §1's static model reads 67%, because
    # this one charges batch steps at full batch_max fill and does not model
    # rework, both optimistic -- but the LITHO families, which are what the
    # capacity check is actually about. Those come out at 85-86% against
    # 0012's 83-88%, i.e. the two models agree where it matters.
    real_fams = [f for f in instance.family_machines
                 if groups_by_family.get(f) not in PSEUDO_GROUPS]
    real_tools = sum(len(instance.family_machines[f]) for f in real_fams)
    fabwide = (sum(per_lot.get((f, p), 0.0) * rate.get(p, 0.0)
                   for f in real_fams for p in parts)
               / (real_tools * SECONDS_PER_DAY))
    print(f'\n    fab-wide pristine load {fabwide*100:.1f}% over '
          f'{real_tools} real tools ({len(instance.machines)} incl. the '
          f'{"/".join(PSEUDO_GROUPS)} pseudo-family); {len(table)} '
          f'(family, part) pairs constrained across '
          f'{len({f for f, _ in table})} families')
    if orphans_fixed:
        print(f'    floor applied: {orphans_fixed} tool(s) had no part and were '
              'qualified for the least-covered one')
    for n_ in notes:
        print(f'    note: {n_}')

    if refused:
        print()
        for fam, pristine, eff, worst in refused:
            print(f'  REFUSED: {fam} {pristine*100:.1f}% pristine -> '
                  f'{eff*100:.1f}% effective (limit {args.max_util*100:.0f}%) '
                  'on parts ' + ','.join(worst))
        print('\n  Nothing written. ADR 0013 §4: drop --fraction rather than the '
              'floors.\n  A matrix that deletes capacity turns a dispatching '
              'experiment into a\n  capacity one, and every rule collapses '
              'together on the row.')
        return None

    prov = {
        'generator': 'bench/tools/gen_overlay.py',
        'generator_version': GENERATOR_VERSION,
        'dataset': args.dataset,
        'scope': args.scope,
        'fraction': args.fraction,
        'overlay_seed': args.overlay_seed,
        'skew': bool(args.skew),
        'max_util': args.max_util,
        'starts_scale_checked': args.starts_scale,
        'floors': {'min_tools_per_pair': 2, 'every_tool_qualified': True},
        'machine_naming': '<STNFAM>#<ordinal within family, file order>',
        'pseudo_groups_excluded': [] if args.include_delay else list(PSEUDO_GROUPS),
        'rework_modelled': False,
        'pairs': len(table),
        'families_constrained': sorted({f for f, _ in table}),
        'capacity': {fam: {'tools': n, 'qualified_per_part': k,
                           'pristine_util': round(pristine, 4),
                           'effective_util': round(eff, 4)}
                     for fam, n, k, pristine, eff, _ in rows},
        'fabwide_pristine_util': round(fabwide, 4),
    }
    ov = Overlay(args.name, table, prov)
    ov.hash = overlay_mod.table_hash(ov.table)
    d = ov.write(args.root)
    print(f'\n  wrote {os.path.relpath(d, REPO)}  hash {ov.hash}')
    return ov


def main():
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    sim_runner.add_common_args(p, days_default=1)
    p.add_argument('--name', required=True, help='overlay directory name')
    p.add_argument('--scope', default='all',
                   help="'all', or station groups / aliases: litho, implant, "
                        "diffusion, etch, or a literal STNGRP (default: all)")
    p.add_argument('--fraction', type=float, default=0.50,
                   help='share of a family qualified per part (default: 0.50)')
    p.add_argument('--overlay-seed', type=int, default=7,
                   help='SEPARATE from --seed: the matrix is a property of the '
                        'dataset, identical for every rule and every run seed')
    p.add_argument('--skew', action='store_true',
                   help='deliberately unbalanced variant (ADR 0013 §3.2)')
    p.add_argument('--starts-scale', type=float, default=1.03,
                   help='start rate the capacity check is made at (ADR 0012 '
                        'operating point)')
    p.add_argument('--max-util', type=float, default=0.90,
                   help='refuse to write above this effective utilisation')
    p.add_argument('--include-delay', action='store_true',
                   help='also constrain the Delay_32 transport pseudo-family')
    p.add_argument('--rate-horizon-days', type=int, default=120,
                   help='horizon the instance is BUILT over so the start rate '
                        'can be read off real future lots (nothing is simulated)')
    p.add_argument('--root', default=None, help='overlay root (testing)')
    a = p.parse_args()
    a.dataset = sim_runner.normalize_dataset(a.dataset)
    if build(a) is None:
        sys.exit(1)


if __name__ == '__main__':
    main()
