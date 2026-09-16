#!/usr/bin/env python3
"""Quick paired A/B of hold-before-entry from an existing warmed checkpoint.

Resumes the SAME no-hold warm-up twice -- once with the gate off, once on --
and runs DAYS more days. Same fab, same random state, so the difference is the
gate. A smoke test for "does it work", not a publishable row: the warm-up was
built without holds.

    hold_ab.py <scale-tag e.g. cqt5r0c | cqtr0c> <seed> <hold_frac|off> [days]
"""
import json
import os
import sys
import time

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
for p in ('bench/tools', 'baselines/pyscfabsim', 'baselines/pyscfabsim/simulation'):
    sys.path.insert(0, os.path.join(REPO, p))
os.environ.setdefault('SIM_CONTROL_FILE', '/dev/null')
os.environ['QT_PROMOTE_FRAC'] = '0.50'

import cloudpickle                                      # noqa: E402
from randomizer import Randomizer                       # noqa: E402
import sim_runner                                       # noqa: E402

tag, seed, hold = sys.argv[1], int(sys.argv[2]), sys.argv[3]
days = float(sys.argv[4]) if len(sys.argv) > 4 else 15
rule = sys.argv[5] if len(sys.argv) > 5 else 'qt'
rule_obj = rule
if rule == 'crit':
    import crit_sched                                   # noqa: E402
    rule_obj = crit_sched.CritSched()
path = os.path.join(REPO, 'bench', 'snapshots',
                    f'SMT2020_LVHM_seed{seed}_qt_Demand_day90_{tag}_qp050_h180.ckpt')
blob = cloudpickle.load(open(path, 'rb'))
inst = blob['instance']
Randomizer().random.setstate(blob['random'])
inst.plugins = []
inst.cqt_hold_frac = None if hold == 'off' else float(hold)

t0, w0 = inst.current_time, time.time()
d0, s0, v0 = len(inst.done_lots), inst.counter_cqt_scrapped, inst.counter_cqt_violated
wip0 = len(inst.active_lots)
sim_runner.run(inst, t0 + days * 86400, rule_obj, stream=open(os.devnull, 'w'))
shipped = len(inst.done_lots) - d0
scrapped = inst.counter_cqt_scrapped - s0
out = {
    'tag': tag, 'seed': seed, 'hold': hold, 'days': days, 'rule': rule,
    'shipped_per_day': shipped / days, 'scrapped_per_day': scrapped / days,
    'scrap_share': scrapped / max(1, shipped + scrapped),
    'violations': inst.counter_cqt_violated - v0,
    'wip_start': wip0, 'wip_end': len(inst.active_lots),
    'hold_events': inst._hold_state()['held'] if hold != 'off' else 0,
    'wall_s': round(time.time() - w0),
    'rule_stats': dict(getattr(rule_obj, 'stats', {}) or {}),
}
print(json.dumps(out))
