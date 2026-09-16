#!/usr/bin/env python3
"""One planning round of crit on a warmed checkpoint: what the model sees and plans.

    crit_debug.py <scale-tag e.g. cqtr0c> <seed>
"""
import os
import sys

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
for p in ('bench/tools', 'baselines/pyscfabsim', 'baselines/pyscfabsim/simulation'):
    sys.path.insert(0, os.path.join(REPO, p))
os.environ.setdefault('SIM_CONTROL_FILE', '/dev/null')
os.environ['QT_PROMOTE_FRAC'] = '0.50'
import cloudpickle                                      # noqa: E402
import crit_sched                                       # noqa: E402

tag, seed = sys.argv[1], int(sys.argv[2])
blob = cloudpickle.load(open(os.path.join(
    REPO, 'bench', 'snapshots', f'SMT2020_LVHM_seed{seed}_qt_Demand_day90_{tag}_qp050_h180.ckpt'), 'rb'))
inst = blob['instance']
r = crit_sched.CritSched()
r.bind(inst)
now = inst.current_time
r._replan(now)
for fam, d in r.debug.items():
    print(fam, d)
    for t in inst.family_machines[fam]:
        q = r.plan.get(t.idx, [])
        print('   tool', t.idx, 'free' if inst.free_machines[t.idx] else 'busy',
              'waiting', len(t.waiting_lots),
              'plan', [(round((s - now) / 60), len(m)) for s, m in q])
