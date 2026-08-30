"""
tool_probe -- per-tool behaviour from a PySCFabSim run.

stats.py already tracks busy/setup/PM/breakdown time per machine, then averages
it away by family. This keeps it per machine, adds the two idle buckets the
simulator does not separate (starved vs blocked), and can replay the decision
sequence for one tool.

This is the headless view: run the whole window as fast as possible and report
what happened. Watching a tool while it runs is the dashboard's job -- there
was a --follow mode here that drew its own terminal view with its own pacing,
a second implementation of what dispatch/ui already does over the API, and it
is gone. Feed the dashboard with sim_feed.py instead.

Lives in bench/ on purpose: baselines/pyscfabsim is vendored read-only, so this
constructs FileInstance itself rather than editing greedy.py's plugin list.
sim_runner does the bootstrapping and owns the dispatch loop.

Needs the baseline's interpreter (pandas, matplotlib); .venv is gitignored, so
see baselines/pyscfabsim/UPSTREAM.md if it is not built yet.

  baselines/pyscfabsim/.venv/bin/python3 bench/tools/tool_probe.py --days 400 --top 15
  baselines/pyscfabsim/.venv/bin/python3 bench/tools/tool_probe.py --days 400 --tool 970

Time budget per tool, over the measured window:

  busy    processing lots
  setup   changeover
  pm      preventive maintenance
  down    unplanned breakdown
  block   idle WITH lots queued    <- a constraint somewhere else
  starve  idle with an empty queue <- not the bottleneck, whatever it feels like

busy+setup high and starve low  => this tool is the constraint.
starve high                     => it is waiting on something upstream.
"""
import argparse
import sys
from collections import defaultdict

# Must precede the baseline imports: it puts the vendored simulator on the path
# and chdirs into it.
import sim_runner  # noqa: E402
from sim_runner import RESET_AT, SECONDS_PER_DAY  # noqa: E402

from plugins.interface import IPlugin  # noqa: E402



class ToolProbe(IPlugin):
    """Accumulates the per-machine idle split and a decision log."""

    def __init__(self, focus=None, log_limit=5000):
        self.focus = focus          # machine idx to record decisions for
        self.log_limit = log_limit
        self.decisions = []

    def on_sim_init(self, instance):
        self.instance = instance
        n = len(instance.machines)
        self.free_since = [None] * n     # when the tool last went idle
        self.queue_at_free = [0] * n     # lots waiting at that moment
        self.blocked = [0.0] * n
        self.starved = [0.0] * n
        self.dispatches = [0] * n
        self.lots_out = [0] * n
        self.setup_changes = [0] * n
        # instance.dispatch() updates machine.current_setup BEFORE calling
        # on_dispatch, so the machine cannot tell us what it changed away from.
        # Track the previous value here instead.
        self.last_setup = [None] * n
        self.queue_samples = defaultdict(list)
        self.window_start = 0.0

    def note_reset(self, t):
        """The 1-year ResetEvent zeroes the machine counters; match it here so
        the two halves of the time budget cover the same window."""
        n = len(self.instance.machines)
        self.blocked = [0.0] * n
        self.starved = [0.0] * n
        self.dispatches = [0] * n
        self.lots_out = [0] * n
        self.setup_changes = [0] * n
        # last_setup is deliberately NOT cleared: the tool's physical setup
        # carries across the reset even though the counters do not.
        self.queue_samples.clear()
        self.decisions.clear()
        self.window_start = t
        self.free_since = [t if f is not None else None for f in self.free_since]

    def on_machine_free(self, instance, machine):
        i = machine.idx
        self.free_since[i] = instance.current_time
        self.queue_at_free[i] = len(machine.waiting_lots)
        self.queue_samples[i].append(len(machine.waiting_lots))

    def on_dispatch(self, instance, machine, lots, machine_end_time, lot_end_time):
        i = machine.idx
        t = instance.current_time
        self._close_idle(i, t)
        self.dispatches[i] += 1
        self.lots_out[i] += len(lots)

        setup_to = machine.current_setup
        setup_from = self.last_setup[i]
        if setup_from is not None and setup_to and setup_to != setup_from:
            self.setup_changes[i] += 1
        self.last_setup[i] = setup_to

        if self.focus is not None and i == self.focus and len(self.decisions) < self.log_limit:
            self.decisions.append({
                't': t,
                'queue': self.queue_at_free[i],
                'picked': [(l.name, l.actual_step.step_name) for l in lots],
                'batch': len(lots),
                'setup_from': setup_from,
                'setup_to': setup_to,
                'busy_until': machine_end_time,
            })

    def _close_idle(self, i, t):
        if self.free_since[i] is None:
            return
        idle = t - self.free_since[i]
        if idle > 0:
            if self.queue_at_free[i] > 0:
                self.blocked[i] += idle
            else:
                self.starved[i] += idle
        self.free_since[i] = None

    def on_sim_done(self, instance):
        t = instance.current_time
        for i in range(len(instance.machines)):
            self._close_idle(i, t)


def row_for(probe, m, span):
    """The time budget for one machine, normalised to the window."""
    i = m.idx
    q = probe.queue_samples.get(i, [])
    return {
        'idx': i,
        'family': m.family,
        'group': m.group,
        'busy': m.utilized_time / span,
        'setup': m.setuped_time / span,
        'pm': m.pmed_time / span,
        'down': m.bred_time / span,
        'block': probe.blocked[i] / span,
        'starve': probe.starved[i] / span,
        'disp': probe.dispatches[i],
        'lots': probe.lots_out[i],
        'chg': probe.setup_changes[i],
        'q_avg': (sum(q) / len(q)) if q else 0.0,
        'q_max': max(q) if q else 0,
    }


def rows(probe, instance):
    """One dict per machine: the time budget, normalised to the window."""
    span = instance.current_time - probe.window_start
    if span <= 0:
        return []
    return [row_for(probe, m, span) for m in instance.machines]


def is_delay(r):
    """Delay_* families are queue-time placeholders, not equipment. They sit at
    ~100% busy by construction and bury the real tools if left in the ranking."""
    return r['family'].startswith('Delay')


def print_top(rs, n):
    rs = sorted(rs, key=lambda r: r['busy'] + r['setup'], reverse=True)[:n]
    print()
    print('  Ranked by busy+setup. "block" is idle with lots queued -- that is')
    print('  the column that means a constraint, not just a loaded tool.')
    print()
    hdr = (f"{'tool':>6}  {'family':<14} {'busy':>6} {'setup':>6} {'pm':>5} "
           f"{'down':>5} {'block':>6} {'starve':>7} {'disp':>7} {'lots':>7} "
           f"{'chg':>6} {'q_avg':>6} {'q_max':>6}")
    print(hdr)
    print('  ' + '-' * (len(hdr) - 2))
    for r in rs:
        print(f"{r['idx']:>6}  {r['family'][:14]:<14} "
              f"{r['busy']*100:>5.1f}% {r['setup']*100:>5.1f}% {r['pm']*100:>4.1f}% "
              f"{r['down']*100:>4.1f}% {r['block']*100:>5.1f}% {r['starve']*100:>6.1f}% "
              f"{r['disp']:>7} {r['lots']:>7} {r['chg']:>6} "
              f"{r['q_avg']:>6.1f} {r['q_max']:>6}")
    print()


def print_bar(r):
    """One tool, one line of accounted time. Segment widths are truncated, not
    rounded, so the bar never claims more than the window."""
    seg = [('#', r['busy']), ('S', r['setup']), ('P', r['pm']),
           ('X', r['down']), ('B', r['block']), ('.', r['starve'])]
    # Fractions can exceed 1 on a window of near-zero length (the first moments
    # of a run, or just after the reset), so clamp per segment and in total.
    bar = ''
    for ch, v in seg:
        w = max(0, min(60 - len(bar), int(max(0.0, v) * 60)))
        bar += ch * w
    print(f"  [{bar:<60}]")
    print('   # busy   S setup   P pm   X down   B blocked   . starved')


def print_focus(probe, rs, tail):
    i = probe.focus
    r = next((x for x in rs if x['idx'] == i), None)
    if r is None:
        print(f'  tool {i} not found')
        return
    print()
    print(f"  TOOL {i}   family={r['family']}   group={r['group']}")
    print()
    print(f"    busy    {r['busy']*100:5.1f}%")
    print(f"    setup   {r['setup']*100:5.1f}%   ({r['chg']} changeovers)")
    print(f"    pm      {r['pm']*100:5.1f}%")
    print(f"    down    {r['down']*100:5.1f}%")
    print(f"    blocked {r['block']*100:5.1f}%   idle with lots queued")
    print(f"    starved {r['starve']*100:5.1f}%   idle, empty queue")
    print()
    print_bar(r)
    print()
    print(f"    {r['disp']} dispatches, {r['lots']} lots, "
          f"queue avg {r['q_avg']:.1f} max {r['q_max']}")
    print()
    if not probe.decisions:
        print('    no decisions recorded for this tool')
        return
    print(f'  last {min(tail, len(probe.decisions))} decisions:')
    print()
    print(f"    {'day':>8} {'queue':>6} {'batch':>6}  {'setup':<18} step / lot")
    print('    ' + '-' * 74)
    for d in probe.decisions[-tail:]:
        chg = f"{d['setup_from'] or '-'}->{d['setup_to'] or '-'}"
        if d['setup_from'] == d['setup_to']:
            chg = f"({d['setup_to'] or '-'})"
        first = d['picked'][0] if d['picked'] else ('', '')
        print(f"    {d['t']/86400:>8.2f} {d['queue']:>6} {d['batch']:>6}  "
              f"{chg[:18]:<18} {first[1][:24]:<24} {first[0][:16]}")
    print()


def main():
    p = argparse.ArgumentParser(
        description='per-tool behaviour from a PySCFabSim run')
    sim_runner.add_common_args(p, days_default=400)
    p.add_argument('--top', type=int, default=15)
    p.add_argument('--tool', type=int, default=None, help='drill into one machine idx')
    p.add_argument('--tail', type=int, default=30, help='decisions to show for --tool')
    p.add_argument('--include-delay', action='store_true',
                   help='keep Delay_* pseudo-tools in the ranking')
    a = p.parse_args()

    a.dataset = sim_runner.normalize_dataset(a.dataset)

    print(f'  loading {a.dataset}, {a.days} days, rule={a.dispatcher}, seed={a.seed}',
          file=sys.stderr)

    probe = ToolProbe(focus=a.tool)
    instance, run_to = sim_runner.build(
        a.dataset, a.days, a.seed, [probe], a.batch_strat)

    # Match greedy.py: after one year the machine counters are zeroed, so the
    # numbers describe steady state rather than the fill-up transient. The
    # probe's own counters have to be zeroed at the same instant, or the two
    # halves of the time budget would cover different windows.
    from events import ResetEvent
    use_reset = a.days > 365
    if use_reset:
        instance.add_event(ResetEvent(RESET_AT))
    reset_done = not use_reset

    def before_dispatch(inst):
        nonlocal reset_done
        if not reset_done and inst.current_time >= RESET_AT:
            probe.note_reset(inst.current_time)
            reset_done = True

    interrupted = sim_runner.run(instance, run_to, a.dispatcher,
                                 before_dispatch=before_dispatch,
                                 stream=sys.stdout)

    if not interrupted:
        instance.finalize()
    probe.on_sim_done(instance)

    span = instance.current_time - probe.window_start
    print(f'\n  {a.dataset}  rule={a.dispatcher}  seed={a.seed}  '
          f'window={span/SECONDS_PER_DAY:.1f} days of '
          f'{instance.current_time/SECONDS_PER_DAY:.1f} simulated')
    if not use_reset:
        print('  NOTE: run is <=365 days, so no warm-up reset. Numbers include the '
              'fill-up\n        transient and are not comparable to the 730-day results.')

    rs = rows(probe, instance)
    if a.tool is not None:
        # Never filter a tool the caller asked for by number.
        print_focus(probe, rs, a.tail)
    else:
        if not a.include_delay:
            n_before = len(rs)
            rs = [r for r in rs if not is_delay(r)]
            print(f'  {n_before - len(rs)} Delay_* pseudo-tools hidden '
                  f'(--include-delay to show)')
        print_top(rs, a.top)


if __name__ == '__main__':
    main()
