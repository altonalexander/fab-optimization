#!/usr/bin/env python3
"""Usage: audit_objective.py [checkpoint.ckpt]

Audit every term of the solver objective on a real warmed fab.

Loads the seed-0 qt-warmed checkpoint (day 90, cqt scale 10), takes every
lot waiting somewhere, and evaluates -- with the rule's own code -- every
multiplier that goes into `urgency`, the queue-time boost on both sides of
the boundary, the time cost, and the unassigned penalty. Then reports the
distribution of each and a variance decomposition of log(cost), so we can
see which terms actually move the objective and which are inert.
"""
import math
import os
import statistics as st
import sys

REPO = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..'))
for p in ('bench/tools', 'baselines/pyscfabsim', 'baselines/pyscfabsim/simulation'):
    sys.path.insert(0, os.path.join(REPO, p))
os.environ.setdefault('SIM_CONTROL_FILE', '/dev/null')
os.environ['QT_PROMOTE_FRAC'] = '0.50'

import cloudpickle  # noqa: E402
import slate_rule  # noqa: E402
from slate_rule import qtime_slack_s, QTIME_INERT  # noqa: E402

CK = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
    REPO, 'bench/snapshots/SMT2020_LVHM_seed0_qt_Demand_day90_cqt10r3_h270.ckpt')
blob = cloudpickle.load(open(CK, 'rb'))
inst = blob['instance']
inst.plugins = []
inst.cqt_enforce, inst.cqt_scale = True, 10.0
t = inst.current_time
print(f'checkpoint day {t / 86400:.1f}, active lots {len(inst.active_lots)}')

rule = slate_rule.SlateRule(inst, solver='cpsat', pressure='full', fallback='qt', horizon_s=0.0)
lots = rule._ready_lots(inst)
rule._family_wip = slate_rule._family_counts(lots)
print(f'waiting lots {len(lots)}')


def pct(v, q):
    v = sorted(v)
    return v[min(len(v) - 1, int(q * len(v)))]


def summ(name, v, fmt='{:.3g}'):
    v = [x for x in v if x is not None and not math.isnan(x)]
    if not v:
        print(f'{name:34s} (empty)')
        return
    neq = sum(1 for x in v if abs(x - 1.0) > 1e-9)
    print(f'{name:34s} n={len(v):5d}  min {fmt.format(min(v)):>8}  p10 {fmt.format(pct(v, .1)):>8}  '
          f'p50 {fmt.format(pct(v, .5)):>8}  p90 {fmt.format(pct(v, .9)):>8}  max {fmt.format(max(v)):>8}  '
          f'!=1: {100 * neq / len(v):5.1f}%')


rows = []
for lot in lots:
    step = lot.actual_step
    base = max(float(lot.priority), 0.01)
    cr = lot.cr(t)
    due1 = 1.0 + max(0.0, 2.0 - cr)
    due2 = min(50.0, 1.0 / max(cr, 0.02)) if cr < 1.0 else 1.0
    wait = max(0.0, t - (lot.free_since or t))
    age = 1.0 + min(1.0, wait / 604800.0)
    rem = lot.remaining_steps
    nxt = rem[0] if rem else None
    if nxt is not None:
        ahead = rule._family_wip.get(nxt.family, 0)
        cap = max(1, len(inst.family_machines.get(nxt.family, ())))
        load = ahead / cap
        down = max(0.8, 1.25 - 0.09 * min(load, 5.0))
    else:
        load, down = 0.0, 1.0
    batch = 1.0
    if step.batch_max and step.batch_max > 1:
        if rule._family_wip.get(step.family, 0) >= (step.batch_min or 1):
            batch = 1.1
    u_rule = rule._urgency(lot, t)
    u_mine = base * due1 * due2 * age * down * batch
    assert abs(u_rule - u_mine) < 1e-6 * max(1, u_rule), (u_rule, u_mine)
    # Checkpoint predates lot.cqt_window_s: derive the window from the step
    # that opened it, exactly as instance.py would have stored it.
    if lot.cqt_waiting is not None and not getattr(lot, 'cqt_window_s', None) \
            and getattr(lot, 'cqt_open_step', None) is not None:
        lot.cqt_window_s = lot.cqt_open_step.cqt_time * inst.cqt_scale
    slack = qtime_slack_s(lot, t)                 # window-relative pseudo-seconds, or inert
    inert = slack >= QTIME_INERT
    boost_cost = 1.0 + 600.0 / max(slack, 60.0)
    boost_pen = 1.0 + 3600.0 / max(slack, 60.0)
    proc = step.processing_time.avg()
    # setup range over the family's tools right now
    setups = []
    for m in inst.family_machines.get(step.family, ()):
        s = inst.setups.get((m.current_setup or '', step.setup_needed or ''), 0.0) if step.setup_needed else 0.0
        setups.append(float(s or 0.0))
    smin, smax = (min(setups), max(setups)) if setups else (0.0, 0.0)
    cost_min = (smin + proc) / (u_rule * boost_cost)
    cost_max = (smax + proc) / (u_rule * boost_cost)
    pen = max(1.0, float(lot.priority) * boost_pen)
    rows.append(dict(part=lot.part_name, fam=step.family, cr=cr, base=base, due1=due1, due2=due2,
                     age=age, down=down, load=load, batch=batch, u=u_rule, slack=slack, inert=inert,
                     boost_cost=boost_cost, boost_pen=boost_pen, proc=proc, smin=smin, smax=smax,
                     cost_min=cost_min, cost_max=cost_max, pen=pen, wait=wait,
                     open_win=lot.cqt_waiting is not None))

print()
print('=== inputs')
summ('lot.priority (base)', [r['base'] for r in rows])
summ('critical ratio', [r['cr'] for r in rows])
print(f'{"cr < 1 (already late)":34s} {100 * sum(r["cr"] < 1 for r in rows) / len(rows):.1f}% of waiting lots')
summ('wait so far (h)', [r['wait'] / 3600 for r in rows])
summ('process_s (h)', [r['proc'] / 3600 for r in rows])
summ('setup_s min over family (h)', [r['smin'] / 3600 for r in rows])
summ('setup_s max over family (h)', [r['smax'] / 3600 for r in rows])
summ('downstream load (lots/tool)', [r['load'] for r in rows])
n_open = sum(r['open_win'] for r in rows)
n_live = sum(1 for r in rows if not r['inert'])
print(f'{"open q-time window":34s} {n_open} lots ({100 * n_open / len(rows):.1f}%); '
      f'saveable & fed to solver (not inert): {n_live} ({100 * n_live / len(rows):.1f}%)')
print()
print('=== multipliers on urgency (Python side)')
summ('due, gentle  1+max(0,2-cr)', [r['due1'] for r in rows])
summ('due, steep   min(50,1/cr) if cr<1', [r['due2'] for r in rows])
summ('ageing       1+min(1,wait/7d)', [r['age'] for r in rows])
summ('downstream   0.8..1.25', [r['down'] for r in rows])
summ('batch cohort 1 or 1.1', [r['batch'] for r in rows])
summ('URGENCY (product)', [r['u'] for r in rows])
print()
print('=== queue-time boosts (C++ side), on the lots that carry a live window')
live = [r for r in rows if not r['inert']]
summ('slack as fraction of window', [r['slack'] / 600.0 for r in live])
summ('cost boost   1+600/max(s,60)', [r['boost_cost'] for r in live])
summ('penalty boost 1+3600/max(s,60)', [r['boost_pen'] for r in live])
print()
print('=== the objective')
summ('time_cost min (h)', [(r['smin'] + r['proc']) / 3600 for r in rows])
summ('cost() min, s', [r['cost_min'] for r in rows], '{:.4g}')
summ('cost() max, s', [r['cost_max'] for r in rows], '{:.4g}')
summ('unassigned penalty multiplier', [r['pen'] for r in rows])

# variance decomposition of log(cost_min): which terms move it?
print()
print('=== variance of log(cost): share attributable to each log-term (independent-sum approximation)')
terms = {
    'log time_cost': [math.log(r['smin'] + r['proc']) for r in rows],
    'log base priority': [-math.log(r['base']) for r in rows],
    'log due gentle': [-math.log(r['due1']) for r in rows],
    'log due steep': [-math.log(r['due2']) for r in rows],
    'log ageing': [-math.log(r['age']) for r in rows],
    'log downstream': [-math.log(r['down']) for r in rows],
    'log batch': [-math.log(r['batch']) for r in rows],
    'log qtime boost': [-math.log(r['boost_cost']) for r in rows],
}
total = [sum(v[i] for v in terms.values()) for i in range(len(rows))]
vt = st.pvariance(total)
print(f'{"TOTAL var(log cost)":34s} {vt:.4f}')
for k, v in terms.items():
    var = st.pvariance(v)
    cov = sum((a - st.mean(v)) * (b - st.mean(total)) for a, b in zip(v, total)) / len(v)
    print(f'{k:34s} var {var:7.4f}  share-by-cov {100 * cov / vt:6.1f}%')

# Within-family view: the solver only compares lots within one family, so
# the fab-wide variance overstates what time_cost (process time differs by
# family) contributes to any decision. Recompute within families.
print()
print('=== within-family variance share (pooled over families with >= 5 waiting lots)')
by_fam = {}
for i, r in enumerate(rows):
    by_fam.setdefault(r['fam'], []).append(i)
acc = {k: 0.0 for k in terms}
acc_tot = 0.0
nf = 0
for fam, idx in by_fam.items():
    if len(idx) < 5:
        continue
    nf += 1
    tot = [total[i] for i in idx]
    mt = st.mean(tot)
    acc_tot += st.pvariance(tot) * len(idx)
    for k, v in terms.items():
        vv = [v[i] for i in idx]
        mv = st.mean(vv)
        acc[k] += sum((a - mv) * (b - mt) for a, b in zip(vv, tot))
print(f'{nf} families; pooled var(log cost) {acc_tot / len(rows):.4f}')
for k in terms:
    print(f'{k:34s} share-by-cov {100 * acc[k] / acc_tot:6.1f}%')

# How often does the solver's within-family order disagree with pure due-date
# order (cr ascending = most urgent first)? Pairwise discordance per family.
print()
print('=== within-family order: cost() vs critical ratio (pairwise discordance)')
disc = conc = 0
for fam, idx in by_fam.items():
    if len(idx) < 2:
        continue
    for a in range(len(idx)):
        for b in range(a + 1, len(idx)):
            ra, rb = rows[idx[a]], rows[idx[b]]
            dc = ra['cost_min'] - rb['cost_min']      # lower cost = solver prefers
            dr = ra['cr'] - rb['cr']                  # lower cr = due-date prefers
            if dc == 0 or dr == 0:
                continue
            if (dc < 0) == (dr < 0):
                conc += 1
            else:
                disc += 1
print(f'pairs {conc + disc}: agree {100 * conc / (conc + disc):.1f}%  disagree {100 * disc / (conc + disc):.1f}%')
# and against shortest-processing-time order
disc = conc = 0
for fam, idx in by_fam.items():
    if len(idx) < 2:
        continue
    for a in range(len(idx)):
        for b in range(a + 1, len(idx)):
            ra, rb = rows[idx[a]], rows[idx[b]]
            dc = ra['cost_min'] - rb['cost_min']
            dp = ra['proc'] - rb['proc']
            if dc == 0 or dp == 0:
                continue
            if (dc < 0) == (dp < 0):
                conc += 1
            else:
                disc += 1
print(f'vs shortest-processing-time: agree {100 * conc / (conc + disc):.1f}%')

import json
json.dump(rows, open(os.path.join(REPO, 'bench/results/audit_objective_rows.json'), 'w'))
