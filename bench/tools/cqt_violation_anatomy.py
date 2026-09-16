#!/usr/bin/env python3
"""Where does a queue-time violation come from?

Resumes a warmed no-hold checkpoint and follows every window from the moment
it opens (entrance step completes) to the moment it closes (exit step starts),
with NO gate acting -- the hold's estimator runs with an unreachable threshold
so its estimate can be recorded without it ever holding a lot.

For each window:
  open       exit-family queue (lots) and the gate's estimated wait, at opening
  transit    open -> lot joins the exit step's queue (intervening steps + moves)
  queue      joins exit queue -> exit step starts
  violated   start > deadline

The question it answers: were violated lots doomed AT ENTRY (the exit queue was
already too long -- a hold could have helped) or did it go wrong AFTER entry
(queue built, batch waited, transit ran long -- no entrance gate can see it)?

    cqt_violation_anatomy.py <scale-tag e.g. cqtr0c> <seed> [days] [skip_days]
"""
import collections
import json
import os
import statistics as st
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

tag, seed = sys.argv[1], int(sys.argv[2])
days = float(sys.argv[3]) if len(sys.argv) > 3 else 4
skip = float(sys.argv[4]) if len(sys.argv) > 4 else 1
path = os.path.join(REPO, 'bench', 'snapshots',
                    f'SMT2020_LVHM_seed{seed}_qt_Demand_day90_{tag}_qp050_h180.ckpt')
blob = cloudpickle.load(open(path, 'rb'))
inst = blob['instance']
Randomizer().random.setstate(blob['random'])
inst.cqt_hold_frac = 1e12          # estimator live, gate never fires
T0 = inst.current_time
open_rec = {}                      # lot idx -> record
done = []


class Tap(IPlugin):
    def on_lot_free(self, instance, lot):
        if lot.cqt_waiting is None or lot.cqt_deadline is None:
            return
        # A window opened at THIS instant (entrance just completed).
        if abs((lot.cqt_deadline - lot.cqt_window_s) - instance.current_time) > 1e-6:
            if lot.idx in open_rec and 'join' not in open_rec[lot.idx]:
                # freed at an intervening step: not yet at exit
                pass
            return
        fam = None
        for s in lot.remaining_steps:
            if s.order == lot.cqt_waiting:
                fam = s.family
                break
        if fam is None and lot.actual_step is not None and lot.actual_step.order == lot.cqt_waiting:
            fam = lot.actual_step.family
        q = len({l.idx for m in instance.family_machines.get(fam, ()) for l in m.waiting_lots
                 if not isinstance(getattr(l.actual_step, 'cqt_for_step', None), (int, float))})
        open_rec[lot.idx] = {'t_open': instance.current_time, 'W': lot.cqt_window_s, 'fam': fam,
                             'q_open': q, 'est_open': instance.exit_wait_estimate(fam),
                             'direct': lot.actual_step is not None and lot.actual_step.order == lot.cqt_waiting}


inst.plugins = [Tap()]
orig = inst.dispatch


def dispatch(machine, lots):
    now = inst.current_time
    for l in lots:
        r = open_rec.get(l.idx)
        if r is None or l.actual_step is None or l.cqt_waiting is None:
            continue
        if l.actual_step.order != l.cqt_waiting:
            continue
        r.update(t_start=now, t_join=l.free_since, violated=now > l.cqt_deadline,
                 batch=l.actual_step.batch_max > 1, n_in_batch=len(lots),
                 q_start=len({x.idx for m in inst.family_machines.get(machine.family, ())
                              for x in m.waiting_lots}))
        if r['t_open'] >= T0 + skip * 86400:
            done.append(r)
        del open_rec[l.idx]
    return orig(machine, lots)


inst.dispatch = dispatch
sim_runner.run(inst, T0 + days * 86400, 'qt', stream=open(os.devnull, 'w'))


def q(xs, p):
    xs = sorted(xs)
    return xs[int(p * (len(xs) - 1))] if xs else None


H = 3600.0
viol = [r for r in done if r['violated']]
ok = [r for r in done if not r['violated']]
out = {'tag': tag, 'seed': seed, 'windows_closed': len(done), 'violated': len(viol)}


def describe(rs):
    if not rs:
        return {}
    transit = [(r['t_join'] - r['t_open']) / H for r in rs]
    queue = [(r['t_start'] - r['t_join']) / H for r in rs]
    over = [(r['t_start'] - r['t_open'] - r['W']) / H for r in rs]
    return {
        'n': len(rs),
        'window_h_median': q([r['W'] / H for r in rs], .5),
        'transit_h_p50_p90': (q(transit, .5), q(transit, .9)),
        'exit_queue_h_p50_p90': (q(queue, .5), q(queue, .9)),
        'overshoot_h_p50_p90': (q(over, .5), q(over, .9)),
        'share_direct': sum(r['direct'] for r in rs) / len(rs),
        'share_batch_exit': sum(r['batch'] for r in rs) / len(rs),
        'share_est_open_gt_W': sum(1 for r in rs if r['est_open'] is not None and r['est_open'] > r['W']) / len(rs),
        'share_est_unknown': sum(1 for r in rs if r['est_open'] is None) / len(rs),
        'share_transit_alone_gt_W': sum(1 for r in rs if r['t_join'] - r['t_open'] > r['W']) / len(rs),
        'q_open_p50': q([r['q_open'] for r in rs], .5),
    }


out['violated_windows'] = describe(viol)
out['met_windows'] = describe(ok)
# Estimator accuracy: estimate at opening vs the exit-queue wait that followed.
pairs = [(r['est_open'] / H, (r['t_start'] - r['t_join']) / H) for r in done if r['est_open'] is not None]
if pairs:
    ratio = [e / max(a, 1 / 60) for e, a in pairs]
    out['estimator'] = {'n': len(pairs), 'est_h_p50': q([e for e, _ in pairs], .5),
                        'actual_h_p50': q([a for _, a in pairs], .5),
                        'est_over_actual_p10_p50_p90': (q(ratio, .1), q(ratio, .5), q(ratio, .9))}
# Windows still open at the end (censored): how many are already past deadline.
now = inst.current_time
still = [r for r in open_rec.values() if r['t_open'] >= T0 + skip * 86400]
out['still_open'] = {'n': len(still),
                     'past_deadline': sum(1 for r in still if now - r['t_open'] > r['W'])}
# Which exit families carry the violations.
fams = collections.Counter(r['fam'] for r in viol)
out['violations_by_exit_family_top8'] = fams.most_common(8)
# Per family: violations explained by a queue already long at entry?
print(json.dumps(out, indent=1, default=lambda x: round(x, 3) if isinstance(x, float) else str(x)))
