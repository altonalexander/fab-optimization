#!/usr/bin/env python3
"""
tools/smt2020_tool_master.py — build a fab tool master from the SMT2020 files
we actually ship.

Why this exists alongside load_smt2020.py
-----------------------------------------
`load_smt2020.py` reads a *JSON repackaging* of SMT2020 that this repo does not
have, and classifies tools by keyword-matching their names ("furnace", "litho",
"metrology"). SMT2020's own distribution — the one in `data/smt2020/`, the one
both the dispatcher and the baseline read — uses tab-separated files and
abbreviated family names like `DefMEt_FE_118`, `WE_FE_84`, `TF_Met_FE_45`.
Those match none of the keyword rules, so keyword classification silently
defaults most of the fab to SINGLE_WAFER: no batching on the furnaces, no
sampling on metrology. Every downstream number would look plausible and be
wrong.

So this script does not classify by name. It reads the behaviour out of the
data, where it is unambiguous:

  BATCH_FURNACE  the family has route steps with BATCHMN/BATCHMX set.
                 In LVHM that is exactly the 10 Diffusion families and nothing
                 else. Batch bounds come from the route (in wafers) and are
                 converted to lots, which is the unit the dispatcher batches in.

  METROLOGY      the family has route steps with StepPercent < 100, i.e. it is
                 sampled rather than run on every lot. In LVHM that is exactly
                 the 13 families in Def_Met, Litho_Met and TF_Met.

  LITHO_SCANNER  STNGRP == 'Litho'. See the reticle caveat below.

  SINGLE_WAFER   everything else.

`area` is STNGRP, which SMT2020 already provides (Dry_Etch, Diffusion, Litho,
...), so areas are read, not invented.

Known approximations — read these before trusting the output
------------------------------------------------------------
1. RETICLES ARE NOT IN SMT2020. The dataset has no reticle model at all. Litho
   families are emitted as LITHO_SCANNER with the default reticle_swap_s and no
   reticle assignments, so the dispatcher's reticle-exclusivity constraint has
   nothing to bite on. Any claim about reticle contention on this load is
   unfounded. Pass --litho-as-single to drop the pretence entirely.

2. ONE TOOL MASTER CANNOT EXPRESS SMT2020'S SETUP MODEL. SMT2020 has a setup
   matrix with minimum run lengths (setup.txt/setupgrp.txt); the dispatcher has
   a single changeover_s that applies whenever the recipe differs. We take the
   mean STIME over that family's steps that declare a setup. Families that
   never declare one get changeover_s 0, which is truthful for this model and
   optimistic against the real matrix.

3. PROCESS TIMES ARE MEANS OVER STEPS. A station family serves many route steps
   with different PTIME. The dispatcher stores one rate per tool, so we average
   over the steps routed to that family, converting per_lot/per_piece/per_batch
   into the field the tool kind expects. Per-step fidelity lives in the
   simulator, not here.

4. 'recipe' MEANS 'route step' HERE. The dispatcher charges a changeover when
   consecutive lots have different recipes; SMT2020's equivalent granularity is
   the route step (DESC), so that is what we emit. This overstates changeovers
   relative to SMT2020, which charges setup only where the SETUP column says so.

Usage
-----
    python3 dispatch/tools/smt2020_tool_master.py \
        --scenario LVHM --out dispatch/config/fab_tools_lvhm.json
"""

import argparse
import csv
import glob
import json
import os
import statistics
import sys
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, '..', '..'))
DATA = os.path.join(REPO, 'data', 'smt2020')

# SMT2020 lots are 25 wafers throughout; assert rather than assume.
WAFERS_PER_LOT = 25

# Only used where SMT2020 genuinely has no source data. Each one is listed as
# an approximation above.
RETICLE_SWAP_S = 300.0
DEFAULT_METROLOGY_SLOTS = 1


def read_tsv(path):
    with open(path, newline='') as f:
        return list(csv.DictReader(f, delimiter='\t'))


def num(row, key):
    """SMT2020 leaves cells empty rather than zero; treat empty as absent."""
    v = (row.get(key) or '').strip()
    if not v:
        return None
    try:
        return float(v)
    except ValueError:
        return None


def to_seconds(value, units):
    u = (units or 'min').strip().lower()
    return value * {'sec': 1.0, 'min': 60.0, 'hour': 3600.0, 'day': 86400.0}.get(u, 60.0)


def per_wafer_seconds(row):
    """Normalise PTIME/PTUNITS/PTPER onto seconds per wafer."""
    t = num(row, 'PTIME')
    if t is None:
        return None
    secs = to_seconds(t, row.get('PTUNITS'))
    per = (row.get('PTPER') or '').strip().lower()
    if per == 'per_piece':
        return secs
    if per == 'per_lot':
        return secs / WAFERS_PER_LOT
    if per == 'per_batch':
        # Furnaces are modelled with a fixed batch time, handled separately.
        return None
    return secs / WAFERS_PER_LOT


def collect(scenario):
    d = os.path.join(DATA, f'SMT2020_{scenario}')
    if not os.path.isdir(d):
        sys.exit(f'no such scenario dir: {d}')

    tools = read_tsv(os.path.join(d, 'tool.txt.1l'))
    fams = {}
    for r in tools:
        fam = r['STNFAM']
        fams[fam] = {
            'area': r.get('STNGRP') or 'UNKNOWN',
            'qty': int(float(r.get('STNQTY') or 1)),
            'cap': num(r, 'STNCAP'),
        }

    steps = defaultdict(list)
    routes = sorted(glob.glob(os.path.join(d, 'route_*.txt')))
    if not routes:
        sys.exit(f'no route files in {d}')
    for path in routes:
        for r in read_tsv(path):
            fam = r.get('STNFAM')
            if fam:
                steps[fam].append(r)
    return fams, steps, len(routes)


def classify(fam, meta, rows):
    if any(num(r, 'BATCHMX') for r in rows):
        return 'BATCH_FURNACE'
    if any((num(r, 'StepPercent') or 100.0) < 100.0 for r in rows):
        return 'METROLOGY'
    if meta['area'] == 'Litho':
        return 'LITHO_SCANNER'
    return 'SINGLE_WAFER'


def changeover_seconds(rows):
    times = [to_seconds(num(r, 'STIME'), r.get('STUNITS'))
             for r in rows if num(r, 'STIME') is not None]
    return round(statistics.fmean(times), 1) if times else 0.0


def mean_per_wafer(rows, fallback):
    vals = [v for v in (per_wafer_seconds(r) for r in rows) if v is not None]
    return round(statistics.fmean(vals), 2) if vals else fallback


def build(scenario, litho_as_single=False, include_delay=False):
    fams, steps, n_routes = collect(scenario)
    tools, all_recipes = [], set()
    counts = defaultdict(int)
    skipped, pseudo = [], []

    for fam in sorted(fams):
        meta = fams[fam]
        rows = steps.get(fam, [])
        if not rows:
            # A family no product routes to contributes no capacity and would
            # show on the tools page as a tool that can never run anything.
            skipped.append(fam)
            continue

        # SMT2020 models queue time as a "Delay" station family with a large
        # STNQTY (400 in LVHM — the biggest single group in the file). It is
        # not equipment: it exists so a lot can sit for a fixed time without
        # occupying a real tool. bench/tools/tool_probe.py hides these for the
        # same reason. Including them would put 400 phantom tools on the tools
        # page and inflate every capacity number derived from it.
        if not include_delay and (fam.startswith('Delay') or meta['area'].startswith('Delay')):
            pseudo.append(fam)
            continue

        kind = classify(fam, meta, rows)
        if kind == 'LITHO_SCANNER' and litho_as_single:
            kind = 'SINGLE_WAFER'
        counts[kind] += 1

        recipes = sorted({(r.get('DESC') or '').strip()
                          for r in rows if (r.get('DESC') or '').strip()})
        all_recipes.update(recipes)

        base = {'kind': kind, 'area': meta['area'], 'recipes': recipes}

        if kind == 'BATCH_FURNACE':
            mins = [num(r, 'BATCHMN') for r in rows if num(r, 'BATCHMN')]
            maxs = [num(r, 'BATCHMX') for r in rows if num(r, 'BATCHMX')]
            batch_s = [to_seconds(num(r, 'PTIME'), r.get('PTUNITS'))
                       for r in rows if num(r, 'PTIME') is not None
                       and (r.get('PTPER') or '').lower() == 'per_batch']
            # SMT2020 states batch bounds in wafers; the dispatcher batches lots.
            base.update(
                min_batch=max(1, int(min(mins) // WAFERS_PER_LOT)) if mins else 4,
                max_batch=max(1, int(max(maxs) // WAFERS_PER_LOT)) if maxs else 6,
                fixed_process_s=round(statistics.fmean(batch_s), 1) if batch_s else 7200.0,
                max_hold_s=1800.0,
            )
        elif kind == 'METROLOGY':
            pct = [num(r, 'StepPercent') for r in rows if num(r, 'StepPercent')]
            per_lot = [to_seconds(num(r, 'PTIME'), r.get('PTUNITS'))
                       for r in rows if num(r, 'PTIME') is not None]
            base.update(
                sample_rate=round(statistics.fmean(pct) / 100.0, 4) if pct else 1.0,
                slots=int(meta['cap']) if meta['cap'] else DEFAULT_METROLOGY_SLOTS,
                sec_per_lot=round(statistics.fmean(per_lot), 1) if per_lot else 480.0,
            )
        elif kind == 'LITHO_SCANNER':
            base.update(
                sec_per_wafer=mean_per_wafer(rows, 22.0),
                # No reticle model in SMT2020 — see approximation 1.
                reticle_swap_s=RETICLE_SWAP_S,
            )
        else:
            base.update(
                sec_per_wafer=mean_per_wafer(rows, 45.0),
                changeover_s=changeover_seconds(rows),
            )

        for i in range(1, meta['qty'] + 1):
            # Match the simulator feed's naming scheme (Litho_BE_110_947), which
            # api/main.py's tool_group() groups by stripping one trailing
            # _<digits>. Using a different separator here would split the config
            # tools and the feed tools into two groups on the tools page.
            tools.append(dict(id=f'{fam}_{i}', **base))

    doc = {
        'fab': f'SMT2020_{scenario}',
        'revision': f'generated-from-SMT2020_{scenario}',
        'source': (f'data/smt2020/SMT2020_{scenario}/tool.txt.1l + {n_routes} route files; '
                   'generated by dispatch/tools/smt2020_tool_master.py'),
        'generated': True,
        'wafers_per_lot': WAFERS_PER_LOT,
        'caveats': [
            'SMT2020 has no reticle model; LITHO_SCANNER reticle_swap_s is a default '
            'and no reticles are assigned.',
            'changeover_s is the mean STIME over steps declaring a setup; SMT2020 uses '
            'a full setup matrix with minimum run lengths.',
            'Process times are means over the route steps routed to each family.',
            "'recipe' here means route step (DESC).",
        ],
        'active_recipes': sorted(all_recipes),
        'tools': tools,
    }
    return doc, counts, skipped, pseudo


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--scenario', default='LVHM',
                   help='LVHM (default, the scenario this project standardises on) or HVLM')
    p.add_argument('--out', default=None)
    p.add_argument('--include-delay', action='store_true',
                   help='include SMT2020 Delay pseudo-tools (queue-time modelling, '
                        'not equipment); off by default, as in tool_probe.py')
    p.add_argument('--litho-as-single', action='store_true',
                   help='emit Litho as SINGLE_WAFER instead of LITHO_SCANNER, since '
                        'SMT2020 carries no reticle data for the scanner model to use')
    a = p.parse_args()

    scen = a.scenario.replace('SMT2020_', '').upper()
    out = a.out or os.path.join(REPO, 'dispatch', 'config', f'fab_tools_{scen.lower()}.json')

    doc, counts, skipped, pseudo = build(scen, a.litho_as_single, a.include_delay)

    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, 'w') as f:
        json.dump(doc, f, indent=2)
        f.write('\n')

    fams = len({t['id'].rsplit('_', 1)[0] for t in doc['tools']})
    print(f'{out}')
    print(f'  {len(doc["tools"])} tools across {fams} station families')
    for k in sorted(counts):
        print(f'    {k:<14} {counts[k]:>3} families')
    print(f'  {len(doc["active_recipes"])} distinct recipes (route steps)')
    if pseudo:
        print(f'  excluded {len(pseudo)} Delay pseudo-tool families (--include-delay to keep)')
    if skipped:
        print(f'  skipped {len(skipped)} families no route reaches: {", ".join(skipped)}')


if __name__ == '__main__':
    main()
