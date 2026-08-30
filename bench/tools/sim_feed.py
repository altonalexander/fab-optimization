"""
sim_feed -- run PySCFabSim as a producer for the dashboard.

This is the producer. Compose once expected a C++ `mes_producer` built from
src/mes_producer_main.cpp, which never existed -- the Dockerfile compiled it
with `|| true`, so the failure was silent and the live panels stayed empty.
That build step and the service are gone: driving the feed from the simulator
gives real routes, setups and breakdowns rather than a synthetic stand-in.

Two modes, matching how the simulator is meant to be used:

  headless   no feed, no pacing -- run as fast as possible for KPIs
             (that is tool_probe.py or the baseline's own main.py)

  feed       emit events as they happen, paced, so the dashboard can show
             near-realtime with pause and fast-forward

Wire format is the one events.hpp uses and api/main.py already decodes, so the
API cannot tell this apart from Kafka:

  {"topic": "fab.lot.events", "payload": "type=LOT_STARTED;lot=...;tool=..."}

Publishes to Kafka. Start the broker first:

  cd dispatch/infra
  docker compose -f docker-compose.yml -f docker-compose.dev.yml \
      --profile data up -d kafka kafka-init

  KAFKA_BROKERS=localhost:29092 make api      # in dispatch/

then feed it, all tools, at ten times realtime:

  baselines/pyscfabsim/.venv/bin/python3 bench/tools/sim_feed.py \
      --days 3 --speed 10

--speed is sim-seconds per wall-second. 1 is realtime, 10 is ten times
realtime (a simulated day takes ~2.4 hours of wall clock), 0 is unpaced.
At --speed 10 the whole fab emits ~6 events/second, which the broker and the
dashboard absorb without effort.

--tool-prefix narrows to one family; omit it for all 1,443 tools.

There is also a hidden --out that writes JSONL instead. It exists only for
scripts/smoke.sh, which has to run without Docker. Kafka is the path.

--from-day / --to-day record a window. A full 730-day run is ~39M events and
4.3 GB; a window is megabytes. The simulation still runs from t=0 either way,
so the window changes what is recorded, never what is simulated.
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

DEFAULT_BROKERS = 'localhost:29092'   # the dev-override host listener

LOT_TOPIC = 'fab.lot.events'
TOOL_TOPIC = 'fab.tool.events'
DECISION_TOPIC = 'fab.dispatch.decisions'


def envelope(**kv):
    """events.hpp wire format: k=v;k=v. Values must not contain ; or =."""
    return ';'.join(f'{k}={v}' for k, v in kv.items() if v is not None)


class FileSink:
    """JSONL to a file the API tails. The line-buffered flush matters: the
    reader is following the file, and buffering would stall the dashboard."""

    def __init__(self, path, truncate):
        self.f = open(path, 'w' if truncate else 'a', buffering=1)
        self.label = path

    def write(self, topic, payload):
        self.f.write(json.dumps({'topic': topic, 'payload': payload}) + '\n')
        self.f.flush()

    def close(self):
        self.f.close()


class KafkaSink:
    """The same events onto the real transport.

    Topic and payload are identical to the file path, so api/main.py's Kafka
    consumer and its FEED_FILE tail decode the same bytes -- that equivalence
    is the point, and it is what lets the file mode be a shortcut rather than a
    fork.
    """

    def __init__(self, brokers):
        from confluent_kafka import Producer
        self.p = Producer({
            'bootstrap.servers': brokers,
            # A dashboard feed must never block the simulation; drop before
            # stalling, and say so at the end rather than silently.
            'queue.buffering.max.messages': 200000,
            'linger.ms': 50,
        })
        self.label = brokers
        self.dropped = 0
        self.failed = 0
        self.delivered = 0

    def _ack(self, err, _msg):
        if err is None:
            self.delivered += 1
        else:
            self.failed += 1

    def write(self, topic, payload):
        try:
            self.p.produce(topic, payload.encode('utf-8'), callback=self._ack)
        except BufferError:
            self.dropped += 1
            self.p.poll(0)
            return
        self.p.poll(0)

    def close(self):
        """Report what was DELIVERED, not what was handed to the client.

        produce() only enqueues. With an unreachable broker every call
        succeeds, the queue fills, and the run ends claiming tens of thousands
        of events while the broker received none -- which is exactly what
        happened the first time this was pointed at a closed port. Anything
        still in the queue after flush() is a failure and is reported as one.
        """
        remaining = self.p.flush(15)
        undelivered = self.failed + self.dropped + (remaining or 0)
        if undelivered:
            print(f'  KAFKA: {self.delivered} delivered, {undelivered} NOT '
                  f'delivered ({self.failed} failed, {self.dropped} dropped, '
                  f'{remaining} still queued at exit) -- is {self.label} '
                  f'reachable?', file=sys.stderr)
        else:
            print(f'  KAFKA: {self.delivered} delivered to {self.label}',
                  file=sys.stderr)


class FeedPlugin(IPlugin):
    """Turns simulator callbacks into the two streams the dashboard reads.

    on_dispatch is the DispatchDecision stream (event-driven); on_machine_free
    and the breakdown hooks are EquipmentState (continuous). They are kept
    separate so a slow equipment view never gates the decision ranking.
    """

    def __init__(self, sink, speed, tool_filter=None, from_day=None, to_day=None):
        self.sink = sink
        self.speed = speed
        self.tool_filter = tool_filter
        # A 730-day run is ~39M events and 4.3 GB. Recording a window instead
        # turns that into megabytes without changing the simulation: the sim
        # still runs from t=0, we just stop writing outside the window.
        self.from_s = None if from_day is None else from_day * 86400
        self.to_s = None if to_day is None else to_day * 86400
        self.last_sim_t = None
        self.emitted = 0
        self.skipped = 0

    def _in_window(self):
        t = getattr(self, '_now', None)
        if t is None:
            return True
        if self.from_s is not None and t < self.from_s:
            return False
        if self.to_s is not None and t > self.to_s:
            return False
        return True

    def _write(self, topic, payload):
        if not self._in_window():
            self.skipped += 1
            return
        self.sink.write(topic, payload)
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

    def _lot_id(self, lot):
        """A unique lot id.

        lot.name is the PRODUCT, not the lot: a whole SMT2020 WIP of 2,226 lots
        carries just four distinct names (Lot_3, Lot_4, HotLot_3, HotLot_4).
        Emitting name alone collapsed every lot of a product onto one key in
        the API's lots_ready/in_flight dicts, so the WIP chart read 0 ready and
        4 in flight after eleven thousand starts. Upstream's own chart_plugin
        writes `lot.name + ' ' + str(lot.idx)` for the same reason.
        """
        return f'{lot.name}_{lot.idx}'

    def on_sim_init(self, instance):
        self._now = instance.current_time
        # Announce every tool once so the dashboard has a roster before any
        # lot moves; otherwise tools only appear as they are first used.
        for m in instance.machines:
            if self.tool_filter and not self._name(m).startswith(self.tool_filter):
                continue
            self._write(TOOL_TOPIC, envelope(
                type='TOOL_STATUS', tool=self._name(m), online=1))

    def on_dispatch(self, instance, machine, lots, machine_end_time, lot_end_time):
        self._now = instance.current_time
        if self.tool_filter and not self._name(machine).startswith(self.tool_filter):
            return
        self._pace(instance.current_time)
        tool = self._name(machine)
        for lot in lots:
            self._write(LOT_TOPIC, envelope(
                type='LOT_STARTED', lot=self._lot_id(lot), tool=tool,
                recipe=lot.actual_step.step_name, prio=1))
        self._write(DECISION_TOPIC, envelope(
            tool=tool, lots=len(lots), queue=len(machine.waiting_lots),
            day=round(instance.current_time / 86400, 4),
            setup=machine.current_setup or '-'))

    def on_lot_done(self, instance, lot):
        self._now = instance.current_time
        self._write(LOT_TOPIC, envelope(type='LOT_COMPLETE', lot=self._lot_id(lot)))

    def on_lots_release(self, instance, lots):
        self._now = instance.current_time
        for lot in lots:
            self._write(LOT_TOPIC, envelope(
                type='LOT_READY', lot=self._lot_id(lot),
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
    sink = p.add_mutually_exclusive_group()
    sink.add_argument('--kafka', metavar='BROKERS', nargs='?',
                      const=DEFAULT_BROKERS, default=None,
                      help=f'publish to Kafka (default: {DEFAULT_BROKERS})')
    # Retained only for scripts/smoke.sh, which has to run with no Docker and
    # no broker. Not the operational path -- hidden from --help so it is not
    # reached for by accident.
    sink.add_argument('--out', help=argparse.SUPPRESS)
    p.add_argument('--dataset', default='SMT2020_HVLM')
    p.add_argument('--days', type=int, default=5)
    p.add_argument('--dispatcher', default='fifo')
    p.add_argument('--seed', type=int, default=0)
    p.add_argument('--speed', type=float, default=600.0,
                   help='sim-seconds per wall-second; 0 = unpaced')
    p.add_argument('--tool-prefix', default=None,
                   help='only emit for tools whose name starts with this '
                        '(e.g. Litho) -- the full fab is ~53k events/sim-day')
    p.add_argument('--from-day', type=float, default=None,
                   help='only emit from this simulated day onward')
    p.add_argument('--to-day', type=float, default=None,
                   help='stop emitting after this simulated day')
    p.add_argument('--truncate', action='store_true',
                   help='start the feed file fresh')
    p.add_argument('--batch-strat', default='Demand',
                   choices=['Max', 'Min', 'RoundRobin', 'Demand'])
    a = p.parse_args()

    if not a.dataset.startswith('SMT2020_'):
        a.dataset = 'SMT2020_' + a.dataset
    if (a.from_day is not None and a.to_day is not None
            and a.from_day > a.to_day):
        p.error('--from-day must not exceed --to-day')

    from dispatching.dispatcher import dispatcher_map
    from file_instance import FileInstance
    from randomizer import Randomizer
    from read import read_all
    from greedy import get_lots_to_dispatch_by_machine

    # Kafka unless a file was explicitly asked for. The transport is the
    # product path; the file exists for tests that cannot start a broker.
    if a.out:
        out = FileSink(a.out, a.truncate)
    else:
        brokers = a.kafka or DEFAULT_BROKERS
        try:
            out = KafkaSink(brokers)
        except ImportError:
            p.error('confluent-kafka is not installed in this interpreter')
        except Exception as e:
            p.error(f'cannot reach Kafka at {brokers}: {e}')

    print(f'  feeding {out.label}  ({a.dataset}, {a.days}d, {a.dispatcher}, '
          f'speed={a.speed:g})', file=sys.stderr)
    if a.tool_prefix:
        print(f'  filtered to tools starting with {a.tool_prefix!r}',
              file=sys.stderr)
    if a.from_day is not None or a.to_day is not None:
        print(f'  window: day {a.from_day if a.from_day is not None else 0}'
              f' .. {a.to_day if a.to_day is not None else a.days}',
              file=sys.stderr)

    files = read_all('datasets/' + a.dataset)
    run_to = 3600 * 24 * a.days
    Randomizer().random.seed(a.seed)

    feed = FeedPlugin(out, a.speed, a.tool_prefix, a.from_day, a.to_day)
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
    msg = (f'  emitted {feed.emitted} events through '
           f'day {instance.current_time/86400:.2f}')
    if feed.skipped:
        msg += f' ({feed.skipped} outside the window)'
    print(msg, file=sys.stderr)


if __name__ == '__main__':
    main()
