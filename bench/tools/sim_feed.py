"""
sim_feed -- run PySCFabSim as a producer for the dashboard.

This is the missing producer. infra/docker-compose.yml expects a `mes_producer`
binary built from src/mes_producer_main.cpp, which does not exist -- the
Dockerfile compiles it with `|| true`, so the failure is silent and the live
panels stay empty. Rather than write a synthetic C++ producer, use the
simulator we already trust as the source of events.

Two modes, matching how the simulator is meant to be used:

  headless   no feed, no pacing -- run as fast as possible for KPIs
             (that is tool_probe.py or the baseline's own main.py)

  feed       emit events as they happen, paced, so the dashboard can show
             near-realtime with pause and fast-forward

Wire format is the one events.hpp uses and api/main.py already decodes, so the
API cannot tell this apart from Kafka:

  {"topic": "fab.lot.events", "payload": "type=LOT_STARTED;lot=...;tool=..."}

Usage:

  # terminal 1
  DEMO_LOTS=1 FEED_FILE=/tmp/fab-feed.jsonl scripts/dev-up.sh
  # terminal 2
  baselines/pyscfabsim/.venv/bin/python3 bench/tools/sim_feed.py \
      --out /tmp/fab-feed.jsonl --days 5 --speed 600

--speed is sim-seconds per wall-second: 600 is ten minutes a second. Use 0 to
emit as fast as the sim runs (fills the dashboard immediately, no pacing).
"""
import argparse
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, '..', '..'))
SIM = os.path.join(REPO, 'baselines', 'pyscfabsim')
sys.path.insert(0, os.path.join(SIM, 'simulation'))
sys.path.insert(0, SIM)
os.chdir(SIM)

from plugins.interface import IPlugin  # noqa: E402

LOT_TOPIC = 'fab.lot.events'
TOOL_TOPIC = 'fab.tool.events'
DECISION_TOPIC = 'fab.dispatch.decisions'


def envelope(**kv):
    """events.hpp wire format: k=v;k=v. Values must not contain ; or =."""
    return ';'.join(f'{k}={v}' for k, v in kv.items() if v is not None)


class FeedPlugin(IPlugin):
    """Turns simulator callbacks into the two streams the dashboard reads.

    on_dispatch is the DispatchDecision stream (event-driven); on_machine_free
    and the breakdown hooks are EquipmentState (continuous). They are kept
    separate so a slow equipment view never gates the decision ranking.
    """

    def __init__(self, out, speed, tool_filter=None):
        self.out = out
        self.speed = speed
        self.tool_filter = tool_filter
        self.last_sim_t = None
        self.emitted = 0

    def _write(self, topic, payload):
        self.out.write(json.dumps({'topic': topic, 'payload': payload}) + '\n')
        self.out.flush()          # the API tails this; buffering would stall it
        self.emitted += 1

    def _pace(self, sim_t):
        if self.speed <= 0:
            return
        if self.last_sim_t is not None:
            delta = (sim_t - self.last_sim_t) / self.speed
            if delta > 0:
                time.sleep(min(delta, 2.0))
        self.last_sim_t = sim_t

    def _name(self, machine):
        return f'{machine.family}_{machine.idx}'

    def on_sim_init(self, instance):
        # Announce every tool once so the dashboard has a roster before any
        # lot moves; otherwise tools only appear as they are first used.
        for m in instance.machines:
            if self.tool_filter and not self._name(m).startswith(self.tool_filter):
                continue
            self._write(TOOL_TOPIC, envelope(
                type='TOOL_STATUS', tool=self._name(m), online=1))

    def on_dispatch(self, instance, machine, lots, machine_end_time, lot_end_time):
        if self.tool_filter and not self._name(machine).startswith(self.tool_filter):
            return
        self._pace(instance.current_time)
        tool = self._name(machine)
        for lot in lots:
            self._write(LOT_TOPIC, envelope(
                type='LOT_STARTED', lot=lot.name, tool=tool,
                recipe=lot.actual_step.step_name, prio=1))
        self._write(DECISION_TOPIC, envelope(
            tool=tool, lots=len(lots), queue=len(machine.waiting_lots),
            day=round(instance.current_time / 86400, 4),
            setup=machine.current_setup or '-'))

    def on_lot_done(self, instance, lot):
        self._write(LOT_TOPIC, envelope(type='LOT_COMPLETE', lot=lot.name))

    def on_lots_release(self, instance, lots):
        for lot in lots:
            self._write(LOT_TOPIC, envelope(
                type='LOT_READY', lot=lot.name,
                recipe=getattr(lot.actual_step, 'step_name', ''),
                prio=1, wafers=25, slack=3600))

    def on_breakdown(self, *args):
        self._tool_event(args, online=0)

    def on_preventive_maintenance(self, *args):
        self._tool_event(args, online=0)

    def _tool_event(self, args, online):
        # interface.py declares on_breakdown(machine, event) but events.py
        # calls it as (instance, event) -- accept either rather than trusting
        # the signature.
        machine = None
        for a in args:
            if hasattr(a, 'machine'):
                machine = a.machine
                break
            if hasattr(a, 'idx') and hasattr(a, 'family'):
                machine = a
                break
        if machine is None:
            return
        if self.tool_filter and not self._name(machine).startswith(self.tool_filter):
            return
        self._write(TOOL_TOPIC, envelope(
            type='TOOL_STATUS', tool=self._name(machine), online=online))


def main():
    p = argparse.ArgumentParser(description='run PySCFabSim as a dashboard feed')
    p.add_argument('--out', required=True,
                   help='JSONL file the API tails (its FEED_FILE)')
    p.add_argument('--dataset', default='SMT2020_HVLM')
    p.add_argument('--days', type=int, default=5)
    p.add_argument('--dispatcher', default='fifo')
    p.add_argument('--seed', type=int, default=0)
    p.add_argument('--speed', type=float, default=600.0,
                   help='sim-seconds per wall-second; 0 = unpaced')
    p.add_argument('--tool-prefix', default=None,
                   help='only emit for tools whose name starts with this '
                        '(e.g. Litho) -- the full fab is ~22k events/sim-day')
    p.add_argument('--truncate', action='store_true',
                   help='start the feed file fresh')
    p.add_argument('--batch-strat', default='Demand',
                   choices=['Max', 'Min', 'RoundRobin', 'Demand'])
    a = p.parse_args()

    if not a.dataset.startswith('SMT2020_'):
        a.dataset = 'SMT2020_' + a.dataset

    from dispatching.dispatcher import dispatcher_map
    from file_instance import FileInstance
    from randomizer import Randomizer
    from read import read_all
    from greedy import get_lots_to_dispatch_by_machine

    mode = 'w' if a.truncate else 'a'
    out = open(a.out, mode, buffering=1)
    print(f'  feeding {a.out}  ({a.dataset}, {a.days}d, {a.dispatcher}, '
          f'speed={a.speed:g})', file=sys.stderr)
    if a.tool_prefix:
        print(f'  filtered to tools starting with {a.tool_prefix!r}',
              file=sys.stderr)

    files = read_all('datasets/' + a.dataset)
    run_to = 3600 * 24 * a.days
    Randomizer().random.seed(a.seed)

    feed = FeedPlugin(out, a.speed, a.tool_prefix)
    instance = FileInstance(files, run_to, True, [feed], None, a.batch_strat)
    rule = dispatcher_map[a.dispatcher]

    try:
        while not instance.done:
            if instance.next_decision_point():
                break
            if instance.current_time > run_to:
                break
            machine, lots = get_lots_to_dispatch_by_machine(instance, rule)
            if lots is None:
                instance.usable_machines.remove(machine)
            else:
                instance.dispatch(machine, lots)
    except KeyboardInterrupt:
        print(f'\n  stopped at day {instance.current_time/86400:.3f}',
              file=sys.stderr)

    out.close()
    print(f'  emitted {feed.emitted} events through '
          f'day {instance.current_time/86400:.2f}', file=sys.stderr)


if __name__ == '__main__':
    main()
