"""
tool_probe -- per-tool behaviour from a PySCFabSim run.

stats.py already tracks busy/setup/PM/breakdown time per machine, then averages
it away by family. This keeps it per machine, adds the two idle buckets the
simulator does not separate (starved vs blocked), and can replay the decision
sequence for one tool.

Lives in bench/ on purpose: baselines/pyscfabsim is vendored read-only, so this
constructs FileInstance itself rather than editing greedy.py's plugin list.

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
import os
import sys
import time
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, '..', '..'))
SIM = os.path.join(REPO, 'baselines', 'pyscfabsim')
# PySCFabSim imports its own modules flat ("from classes import Lot"), so the
# simulation/ dir itself has to be on the path, and datasets/ resolves relative
# to cwd.
sys.path.insert(0, os.path.join(SIM, 'simulation'))
sys.path.insert(0, SIM)
os.chdir(SIM)

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


class Follower:
    """Realtime / playback view of one tool while the sim runs.

    PySCFabSim is discrete-event: it jumps to the next event timestamp and has
    no step loop to throttle. So "realtime" has to be imposed from outside --
    we sleep for the wall-clock equivalent of the sim time that just elapsed.
    --speed is sim-seconds per wall-second; 3600 means an hour a second.
    """

    def __init__(self, probe, tool, speed, pause_every, pause_at_day, tail=8):
        self.probe = probe
        self.tool = tool
        self.speed = speed
        self.pause_every = pause_every
        self.pause_at = pause_at_day * 86400 if pause_at_day else None
        self.tail = tail
        self.last_sim_t = None
        self.since_pause = 0
        self.paused_once = False
        self.interactive = sys.stdin.isatty()

    def pace(self, sim_t):
        if self.speed <= 0:
            return
        if self.last_sim_t is not None:
            delta = (sim_t - self.last_sim_t) / self.speed
            if delta > 0:
                time.sleep(min(delta, 2.0))   # cap so a long jump cannot hang
        self.last_sim_t = sim_t

    def render(self, instance, machine):
        span = instance.current_time - self.probe.window_start
        if span < 3600:
            # Percentages of a window under an hour are noise, not signal.
            sys.stdout.write('\033[2J\033[H')
            print(f"  TOOL {self.tool}  {machine.family}   "
                  f"day {instance.current_time/86400:8.3f}")
            print(f"\n    warming up -- percentages appear after "
                  f"the first simulated hour")
            print(f"\n    queue now {len(machine.waiting_lots)}")
            return
        r = row_for(self.probe, machine, span)
        sys.stdout.write('\033[2J\033[H')     # clear, home
        print(f"  TOOL {r['idx']}  {r['family']}   "
              f"day {instance.current_time/86400:8.3f}   "
              f"speed {self.speed:g}x")
        print()
        print(f"    busy {r['busy']*100:5.1f}%   setup {r['setup']*100:5.1f}%   "
              f"pm {r['pm']*100:4.1f}%   down {r['down']*100:4.1f}%")
        print(f"    blocked {r['block']*100:5.1f}%   starved {r['starve']*100:5.1f}%")
        print()
        print_bar(r)
        print()
        print(f"    queue now {len(machine.waiting_lots):<5} "
              f"avg {r['q_avg']:.1f}  max {r['q_max']}   "
              f"| {r['disp']} dispatches, {r['chg']} changeovers")
        print()
        print('  recent decisions:')
        print(f"    {'day':>9} {'queue':>6} {'batch':>6}  {'setup':<18} step / lot")
        print('    ' + '-' * 74)
        for d in self.probe.decisions[-self.tail:]:
            chg = f"{d['setup_from'] or '-'}->{d['setup_to'] or '-'}"
            if d['setup_from'] == d['setup_to']:
                chg = f"({d['setup_to'] or '-'})"
            first = d['picked'][0] if d['picked'] else ('', '')
            print(f"    {d['t']/86400:>9.3f} {d['queue']:>6} {d['batch']:>6}  "
                  f"{chg[:18]:<18} {first[1][:24]:<24} {first[0][:16]}")
        print()

    def maybe_pause(self, instance, machine):
        """Breakpoint at a decision point: the sim is fully consistent here,
        between one dispatch and the next event."""
        hit = False
        if self.pause_at is not None and not self.paused_once \
                and instance.current_time >= self.pause_at:
            hit, self.paused_once = True, True
        if self.pause_every and self.since_pause >= self.pause_every:
            hit, self.since_pause = True, 0
        if not hit:
            return
        if not self.interactive:
            print('  [paused at decision point -- stdin is not a tty, '
                  'continuing]')
            return
        try:
            input('  [decision point] ENTER to continue, Ctrl-C to stop > ')
        except EOFError:
            pass

    def step(self, instance, machine, dispatched):
        if machine.idx != self.tool:
            return
        if dispatched:
            self.since_pause += 1
        self.pace(instance.current_time)
        self.render(instance, machine)
        self.maybe_pause(instance, machine)


def main():
    p = argparse.ArgumentParser(
        description='per-tool behaviour from a PySCFabSim run')
    p.add_argument('--dataset', default='SMT2020_LVHM')
    p.add_argument('--days', type=int, default=400)
    p.add_argument('--dispatcher', default='fifo')
    p.add_argument('--seed', type=int, default=0)
    p.add_argument('--top', type=int, default=15)
    p.add_argument('--tool', type=int, default=None, help='drill into one machine idx')
    p.add_argument('--tail', type=int, default=30, help='decisions to show for --tool')
    p.add_argument('--follow', action='store_true',
                   help='watch --tool live while the sim runs')
    p.add_argument('--speed', type=float, default=3600.0,
                   help='sim-seconds per wall-second in --follow '
                        '(3600 = an hour a second; 0 = as fast as possible)')
    p.add_argument('--pause-every', type=int, default=0,
                   help='in --follow, pause every N decisions on the tool')
    p.add_argument('--pause-at', type=float, default=None,
                   help='in --follow, pause once at this simulated day')
    p.add_argument('--include-delay', action='store_true',
                   help='keep Delay_* pseudo-tools in the ranking')
    p.add_argument('--batch-strat', default='Demand',
                   choices=['Max', 'Min', 'RoundRobin', 'Demand'])
    a = p.parse_args()

    if not a.dataset.startswith('SMT2020_'):
        a.dataset = 'SMT2020_' + a.dataset

    from dispatching.dispatcher import dispatcher_map
    from file_instance import FileInstance
    from randomizer import Randomizer
    from read import read_all
    from events import ResetEvent
    from greedy import get_lots_to_dispatch_by_machine

    print(f'  loading {a.dataset}, {a.days} days, rule={a.dispatcher}, seed={a.seed}',
          file=sys.stderr)

    files = read_all('datasets/' + a.dataset)
    run_to = 3600 * 24 * a.days
    Randomizer().random.seed(a.seed)

    probe = ToolProbe(focus=a.tool)
    instance = FileInstance(files, run_to, True, [probe], None, a.batch_strat)

    # Match greedy.py: after one year the machine counters are zeroed, so the
    # numbers describe steady state rather than the fill-up transient.
    reset_at = 31536000
    use_reset = a.days > 365
    if use_reset:
        instance.add_event(ResetEvent(reset_at))

    rule = dispatcher_map[a.dispatcher]
    reset_done = not use_reset

    follower = None
    if a.follow:
        if a.tool is None:
            p.error('--follow needs --tool N')
        follower = Follower(probe, a.tool, a.speed, a.pause_every, a.pause_at)

    interrupted = False
    try:
        while not instance.done:
            if instance.next_decision_point():
                break
            if instance.current_time > run_to:
                break
            if not reset_done and instance.current_time >= reset_at:
                probe.note_reset(instance.current_time)
                reset_done = True
            machine, lots = get_lots_to_dispatch_by_machine(instance, rule)
            if lots is None:
                instance.usable_machines.remove(machine)
                dispatched = False
            else:
                instance.dispatch(machine, lots)
                dispatched = True
            if follower is not None:
                follower.step(instance, machine, dispatched)
    except KeyboardInterrupt:
        # Stopping early is a normal way to use --follow, so summarise what
        # ran rather than dumping a traceback.
        interrupted = True
        print('\n  stopped at '
              f'day {instance.current_time/86400:.3f}')

    if not interrupted:
        instance.finalize()
    probe.on_sim_done(instance)

    span = instance.current_time - probe.window_start
    print(f'\n  {a.dataset}  rule={a.dispatcher}  seed={a.seed}  '
          f'window={span/86400:.1f} days of {instance.current_time/86400:.1f} simulated')
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
