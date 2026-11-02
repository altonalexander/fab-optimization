#!/usr/bin/env python3
"""Batch fill and batch wait at the families that carry queue-time violations.

Resumes a warmed no-hold checkpoint (scrap-on-first, qt @ 0.50) and records,
for every batch started at a per_batch family:
  size       lots in the batch vs its min / max
  wait       queue time of each lot in it
  windowed   lots in it that were inside a queue-time window, and how many
             had already blown it

and, hourly, per family: free tools that had lots waiting but could not fire
(no same-route-step group reaching batch_min).

The spiral hypothesis predicts, from scale 8 to scale 1: smaller batches,
longer waits, and more hours with a free furnace and lots queued that it
cannot legally fire.

    batch_fill_probe.py <scale-tag e.g. cqt5r0c> <seed> [days]
"""
import collections
import json
import os
import sys

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
for p in ('bench/tools', 'baselines/pyscfabsim', 'baselines/pyscfabsim/simulation'):
    sys.path.insert(0, os.path.join(REPO, p))
os.environ.setdefault('SIM_CONTROL_FILE', '/dev/null')
os.environ['QT_PROMOTE_FRAC'] = '0.50'

import cloudpickle                                      # noqa: E402
from randomizer import Randomizer                       # noqa: E402
from plugins.interface import IPlugin                   # noqa: E402
import sim_runner                                       # noqa: E402

FOCUS = ('Diffusion_FE_94', 'Diffusion_FE_120', 'Diffusion_BE_123')
tag, seed = sys.argv[1], int(sys.argv[2])
days = float(sys.argv[3]) if len(sys.argv) > 3 else 3
path = os.path.join(REPO, 'bench', 'snapshots',
                    f'SMT2020_LVHM_seed{seed}_qt_Demand_day90_{tag}_qp050_h180.ckpt')
blob = cloudpickle.load(open(path, 'rb'))
inst = blob['instance']
Randomizer().random.setstate(blob['random'])
T0 = inst.current_time
H = 3600.0

batches = collections.defaultdict(list)     # family -> [record]
arrivals = collections.Counter()            # family -> lots joining its queue
stall = collections.defaultdict(lambda: [0, 0])   # family -> [samples, tool-samples stalled]
next_sample = [T0]


class Tap(IPlugin):
    def on_lot_free(self, instance, lot):
        st = lot.actual_step
        if st is not None and st.batch_max > 1:
            arrivals[st.family] += 1


def sample(now):
    while next_sample[0] <= now:
        for fam in FOCUS:
            ms = inst.family_machines.get(fam, ())
            free = [m for m in ms if inst.free_machines[m.idx]]
            groups = collections.Counter()
            for l in {l.idx: l for m in ms for l in m.waiting_lots}.values():
                groups[(l.actual_step.step_name, l.part_name)] += 1
            fireable = any(n >= l_min for (k, n), l_min in
                           ((g, _min_for(fam, g)) for g in groups.items()))
            stall[fam][0] += 1
            if free and groups and not fireable:
                stall[fam][1] += len(free)
        next_sample[0] += H


_min_cache = {}


def _min_for(fam, g):
    return _min_cache.get((fam, g[0]), 1)


inst.plugins = [Tap()]
orig = inst.dispatch


def dispatch(machine, lots):
    now = inst.current_time
    sample(now)
    st = lots[0].actual_step
    if st.batch_max > 1:
        for l in {l.idx: l for m in inst.family_machines.get(machine.family, ()) for l in m.waiting_lots}.values():
            _min_cache[(machine.family, (l.actual_step.step_name, l.part_name))] = l.actual_step.batch_min
        win = [l for l in lots if l.cqt_waiting is not None and l.actual_step.order == l.cqt_waiting]
        batches[machine.family].append({
            'n': len(lots), 'min': st.batch_min, 'max': st.batch_max,
            'wait_h': [(now - l.free_since) / H for l in lots],
            'windowed': len(win),
            'blown': sum(1 for l in win if now > l.cqt_deadline),
        })
    return orig(machine, lots)


inst.dispatch = dispatch
sim_runner.run(inst, T0 + days * 86400, 'qt', stream=open(os.devnull, 'w'))


def q(xs, p):
    xs = sorted(xs)
    return round(xs[int(p * (len(xs) - 1))], 2) if xs else None


out = {'tag': tag, 'seed': seed, 'days': days, 'families': {}}
fams = sorted(batches, key=lambda f: -sum(b['n'] for b in batches[f]))
for fam in fams:
    bs = batches[fam]
    waits = [w for b in bs for w in b['wait_h']]
    rec = {
        'batches_per_day': round(len(bs) / days, 1),
        'lots_per_day': round(sum(b['n'] for b in bs) / days, 1),
        'arrivals_per_day': round(arrivals[fam] / days, 1),
        'fill_vs_max_mean': round(sum(b['n'] / b['max'] for b in bs) / len(bs), 2),
        'share_at_min_only': round(sum(1 for b in bs if b['n'] <= b['min']) / len(bs), 2),
        'lot_wait_h_p50_p90': (q(waits, .5), q(waits, .9)),
        'windowed_lots': sum(b['windowed'] for b in bs),
        'windowed_lots_blown': sum(b['blown'] for b in bs),
    }
    if fam in FOCUS:
        s = stall[fam]
        rec['tools'] = len(inst.family_machines.get(fam, ()))
        rec['stalled_tool_hours_per_day'] = round(s[1] / max(1, s[0]) * 24, 1)
    out['families'][fam] = rec
print(json.dumps(out, indent=1))
