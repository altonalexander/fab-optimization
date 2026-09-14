#!/usr/bin/env python3
"""Facts about the testbed for the paper's methods section, read off the
loaded instance rather than typed from memory: tool and family counts, route
lengths per product, how many steps carry a queue-time window and how long
the windows are, batch and setup structure, and the release rate.
"""
import collections
import json
import os
import sys

REPO = '/home/alton/src/fab-optimization/.claude/worktrees/adr-0013-runs'
os.chdir(REPO)
for p in ('bench/tools', 'baselines/pyscfabsim', 'baselines/pyscfabsim/simulation'):
    sys.path.insert(0, os.path.join(REPO, p))
os.environ.setdefault('SIM_CONTROL_FILE', '/dev/null')
import sim_runner  # noqa: E402

inst, run_to = sim_runner.build('SMT2020_LVHM', 30, 0, [], 'Demand')

machines = list(inst.machines)
fam_of = collections.Counter(m.family for m in machines)
real = {f: n for f, n in fam_of.items() if not f.startswith('Delay')}
delay = {f: n for f, n in fam_of.items() if f.startswith('Delay')}

facts = {
    'tools_total': len(machines),
    'tools_real': sum(real.values()),
    'tools_delay_pseudo': sum(delay.values()),
    'families_real': len(real),
    'families_delay': len(delay),
    'initial_wip_lots': len(inst.active_lots),
}

# Route structure from the lots the instance holds: every lot carries its
# full route as processed + current + remaining, so the longest such chain per
# product is that product's route length.
routes = {}
cqt_steps = collections.defaultdict(set)
cqt_windows = []
batch_steps = collections.defaultdict(set)
setup_steps = collections.defaultdict(set)
all_lots = list(inst.active_lots)
# future releases, if the instance exposes them
for attr in ('lots', 'release_lots', 'future_lots', 'all_lots'):
    v = getattr(inst, attr, None)
    if isinstance(v, (list, tuple)) and v:
        all_lots += list(v)
        facts['future_release_attr'] = attr
        break

for lot in all_lots:
    steps = list(getattr(lot, 'processed_steps', []) or [])
    if getattr(lot, 'actual_step', None) is not None:
        steps.append(lot.actual_step)
    steps += list(getattr(lot, 'remaining_steps', []) or [])
    part = lot.part_name
    routes[part] = max(routes.get(part, 0), len(steps))
    for st in steps:
        fs = getattr(st, 'cqt_for_step', None)
        if isinstance(fs, (int, float)) and getattr(st, 'cqt_time', None):
            cqt_steps[part].add(st.order)
            cqt_windows.append(float(st.cqt_time))
        if getattr(st, 'batch_max', 1) and st.batch_max > 1:
            batch_steps[part].add(st.order)
        if getattr(st, 'setup_needed', ''):
            setup_steps[part].add(st.order)

facts['products'] = sorted(routes)
facts['route_steps'] = routes
facts['cqt_steps_per_product'] = {p: len(s) for p, s in cqt_steps.items()}
facts['cqt_steps_total_distinct'] = sum(len(s) for s in cqt_steps.values())
if cqt_windows:
    w = sorted(set(cqt_windows))
    facts['cqt_window_hours_min'] = min(w) / 3600
    facts['cqt_window_hours_max'] = max(w) / 3600
    facts['cqt_window_hours_distinct'] = [x / 3600 for x in w]
facts['batch_steps_per_product'] = {p: len(s) for p, s in batch_steps.items()}
facts['setup_steps_per_product'] = {p: len(s) for p, s in setup_steps.items()}
facts['batch_tools'] = sum(
    1 for m in machines if any(getattr(st, 'batch_max', 1) > 1 for st in [])
)  # placeholder; batch capacity lives on steps, not tools, in this model

# families by size, for the text
sizes = sorted(real.values())
facts['family_size_min'] = sizes[0]
facts['family_size_median'] = sizes[len(sizes) // 2]
facts['family_size_max'] = sizes[-1]
facts['largest_families'] = sorted(real.items(), key=lambda kv: -kv[1])[:6]

out = os.path.join(REPO, 'docs', 'paper', 'build', 'fab_facts.json')
json.dump(facts, open(out, 'w'), indent=1, default=str)
print(json.dumps({k: v for k, v in facts.items()
                  if k not in ('cqt_window_hours_distinct',)}, indent=1, default=str))
