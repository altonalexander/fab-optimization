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
realtime (a simulated day takes ~2.4 hours of wall clock), 0 is unpaced. The
dashboard's playback menu changes this live: it POSTs to /api/sim/control,
which writes $SIM_CONTROL_FILE (default bench/.sim_control.json), and the
pacing loop here polls that file. Delete the file to fall back to --speed.
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
import signal
import subprocess
import sys
import time
import uuid

# Must precede the baseline imports: it puts the vendored simulator on the path
# and chdirs into it. REPO comes from there too, so there is one definition of
# where the repo root is rather than a copy per consumer.
import sim_runner  # noqa: E402
from sim_runner import REPO  # noqa: E402

from plugins.interface import IPlugin  # noqa: E402

DEFAULT_BROKERS = 'localhost:29092'   # the dev-override host listener

LOT_TOPIC = 'fab.lot.events'
BURNDOWN_TOPIC = 'fab.lot.burndown'
TOOL_TOPIC = 'fab.tool.events'
DECISION_TOPIC = 'fab.dispatch.decisions'
# Compacted. One record per key = the fab as it stands, not its history.
LOT_STATE_TOPIC = 'fab.lot.state'
TOOL_STATE_TOPIC = 'fab.tool.state'
# Compacted, keyed by run. Carries the fab-level KPI series: one KPI_HIST
# record per run with every warm-up sample, then one KPI record per live
# sample. The producer computes these because it is the only party that sees
# the whole simulation from day 0; the mirror only draws them.
KPI_STATE_TOPIC = 'fab.kpi.state'
KPI_SAMPLE_S = 3600        # one sample per simulated hour
KPI_WINDOW_S = 86400       # throughput / cycle time / on-time over a trailing day
TOOLS_FLUSH_S = 6 * 3600   # per-tool books to the run store this often
KPI_FIELDS = ('t', 'wip', 'running', 'util', 'thr', 'ct', 'otd', 'tard',
              'dec', 'opt', 'wq', 'wb', 'wp', 'starts', 'wd')

# Warm-up is ~3 minutes of CPU per 30 simulated days, so a snapshot is worth
# keeping. Keyed by everything that changes the trajectory; a cache hit makes a
# dashboard restart instant instead of re-simulating.
CACHE_DIR = os.path.join(REPO, 'bench', 'snapshots')

# Playback control, written by the API when someone uses the dashboard's speed
# menu and polled here. A file rather than a socket because the feed is a host
# process started by hand: it must survive the API restarting, and there is
# nothing to reconnect to when it is not running. This is playback pacing
# only -- it changes when events are emitted, never what is simulated, so a
# run's trajectory is identical at 1x and 400x.
CONTROL_FILE = os.getenv(
    'SIM_CONTROL_FILE', os.path.join(REPO, 'bench', '.sim_control.json'))
CONTROL_POLL_S = 0.25     # wall seconds between re-reads; the file is tiny


def write_control(speed, paused=False):
    """Publish the feed's current pacing so the dashboard can show and change it.

    Written on start-up so a fresh feed reports its --speed rather than
    whatever the last session left behind.
    """
    try:
        os.makedirs(os.path.dirname(CONTROL_FILE), exist_ok=True)
        tmp = CONTROL_FILE + '.tmp'
        with open(tmp, 'w') as f:
            json.dump({'speed': float(speed), 'paused': bool(paused),
                       'updated': time.time(), 'source': 'sim_feed'}, f)
        os.replace(tmp, CONTROL_FILE)   # atomic: no half-read by the poller
    except OSError:
        pass                            # pacing control is a convenience
# Warm-up history points kept per lot in the snapshot. The raw history is one
# point per completed step, so a 583-step route would carry 583 of them and the
# compacted state topic would hold megabytes per run. Decimated to this many,
# always keeping the first point, the last, and every rework jog -- the jogs
# are the informative part and averaging them away would flatten the one thing
# the history is worth drawing for.
HIST_POINTS = int(os.environ.get('SIM_FEED_HIST_POINTS', '60'))


def decimate(points, cap=HIST_POINTS):
    """Thin a per-lot history to `cap` points, keeping the shape.

    Endpoints and rework jogs are pinned first, then the remainder is filled
    evenly. A jog is a point where steps remaining went *up*: the lot was sent
    back in the line, and that is exactly what a reader is looking for.
    """
    if len(points) <= cap:
        return points
    keep = {0, len(points) - 1}
    for i in range(1, len(points)):
        if points[i][1] > points[i - 1][1]:
            keep.add(i - 1)
            keep.add(i)
    room = cap - len(keep)
    if room > 0:
        stride = max(1, len(points) // (room + 1))
        for i in range(0, len(points), stride):
            if len(keep) >= cap:
                break
            keep.add(i)
    return [points[i] for i in sorted(keep)]


def envelope(**kv):
    """events.hpp wire format: k=v;k=v. Values must not contain ; or =."""
    return ';'.join(f'{k}={v}' for k, v in kv.items() if v is not None)


def cache_path(dataset, seed, dispatcher, day, batch_strat):
    name = f'{dataset}_seed{seed}_{dispatcher}_{batch_strat}_day{day:g}.json'
    return os.path.join(CACHE_DIR, name)


def load_snapshot(path):
    try:
        with open(path) as f:
            return json.load(f)
    except FileNotFoundError:
        return None
    except Exception as e:
        print(f'  cache unreadable ({e}); will re-simulate', file=sys.stderr)
        return None


def save_snapshot(path, snap):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    # Per-process temp name: two feeds resuming the same checkpoint at once
    # (the live feed and a headless baseline) both rewrite this cache, and a
    # shared '.tmp' let one replace() fail on the other's rename.
    tmp = f'{path}.{os.getpid()}.tmp'
    with open(tmp, 'w') as f:
        json.dump(snap, f)
    os.replace(tmp, path)      # atomic: a half-written cache is worse than none


# ---------------------------------------------------------------------------
# Simulator checkpoint.
#
# The JSON snapshot above is the *dashboard's* picture of the fab -- positions
# and history -- and it cannot be simulated from: a discrete-event simulator
# also needs its pending event queue (every process end, breakdown and PM
# already scheduled), every tool's setup state, and the RNG. Without those the
# only way to stream live from day N was to re-simulate days 0..N on every
# start, ~3 min of CPU per 30 days, every time.
#
# So at the warm-up line the whole simulator object is pickled beside the
# snapshot, together with the RNG state and the feed plugin's per-lot books.
# A later start with the same key loads it in seconds and continues from
# exactly where warm-up ended -- the same trajectory a fresh warm-up would
# have produced, because the RNG is restored too.
#
# The file is keyed additionally by the run horizon (`--days`): FileInstance
# generates lot releases only up to run_to, so a checkpoint built for a
# 45-day run cannot honestly serve a 60-day one. A shorter run can reuse a
# longer checkpoint, so lookup takes the smallest sufficient horizon.
# ---------------------------------------------------------------------------
CKPT_FEED_FIELDS = ('_hist', '_cohort_by_lot', '_route_len', '_last_split',
                    '_wear', '_down_until',
                    '_kpi', '_done_log', '_on_tool', '_busy', '_next_kpi_t',
                    '_decisions', '_split_log', '_tool_books', '_busy_since',
                    '_release_log')


def ckpt_path(dataset, seed, dispatcher, day, batch_strat, days):
    name = (f'{dataset}_seed{seed}_{dispatcher}_{batch_strat}'
            f'_day{day:g}_h{int(days)}.ckpt')
    return os.path.join(CACHE_DIR, name)


def find_ckpt(dataset, seed, dispatcher, day, batch_strat, days):
    """The cached checkpoint with the smallest horizon that still covers `days`."""
    import glob
    pat = ckpt_path(dataset, seed, dispatcher, day, batch_strat, 0) \
        .replace('_h0.ckpt', '_h*.ckpt')
    best = None
    for path in glob.glob(pat):
        try:
            h = int(path.rsplit('_h', 1)[1].split('.')[0])
        except (IndexError, ValueError):
            continue
        if h >= days and (best is None or h < best[0]):
            best = (h, path)
    return best[1] if best else None


def save_checkpoint(path, instance, feed, days):
    """Pickle the simulator between two events. Plugins are detached first:
    the feed holds a Kafka producer, which is neither picklable nor wanted."""
    import cloudpickle
    from randomizer import Randomizer
    plugins, instance.plugins = instance.plugins, []
    # The event queue and lot->step->lot references pickle as one long chain;
    # at day 0, before anything has been dispatched, it exceeds the default
    # limit and the checkpoint is silently skipped ("excessively deep
    # recursion required"). Raise it for the dump only.
    old_limit = sys.getrecursionlimit()
    sys.setrecursionlimit(max(old_limit, 100000))
    try:
        blob = {
            'version': 1,
            'sim_t': instance.current_time,
            'days': days,
            'instance': instance,
            'random': Randomizer().random.getstate(),
            'feed': {k: getattr(feed, k) for k in CKPT_FEED_FIELDS},
        }
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = f'{path}.{os.getpid()}.tmp'
        with open(tmp, 'wb') as f:
            cloudpickle.dump(blob, f, protocol=5)
        os.replace(tmp, path)
    finally:
        sys.setrecursionlimit(old_limit)
        instance.plugins = plugins


def load_checkpoint(path, feed):
    """Restore a checkpoint and attach `feed` as its only plugin.

    Returns the instance, or None if the file is unreadable (in which case
    the caller re-simulates; a stale cache must never be fatal).
    """
    try:
        import cloudpickle
        from randomizer import Randomizer
        with open(path, 'rb') as f:
            blob = cloudpickle.load(f)
        if blob.get('version') != 1:
            raise ValueError(f'unknown checkpoint version {blob.get("version")}')
        # Validate everything before touching the plugin. A checkpoint from
        # an older field set once failed halfway through this loop, leaving
        # the warm-up histories from the stale file on the plugin; the fresh
        # re-simulation then appended to them and every lot's past was
        # published twice over.
        # A book the checkpoint predates keeps the plugin's fresh (empty)
        # value: a trailing-day counter is simply short for its first day.
        # Anything else wrong with the blob still fails whole, below.
        books = {k: blob['feed'][k] for k in CKPT_FEED_FIELDS if k in blob['feed']}
        missing = [k for k in CKPT_FEED_FIELDS if k not in blob['feed']]
        if missing:
            print(f'  checkpoint predates {", ".join(missing)}; starting those empty',
                  file=sys.stderr)
        instance = blob['instance']
        Randomizer().random.setstate(blob['random'])
        for k, v in books.items():
            setattr(feed, k, v)
        instance.plugins = [feed]
        return instance
    except Exception as e:
        print(f'  checkpoint unreadable ({e}); will re-simulate', file=sys.stderr)
        return None


def snapshot_of(instance, lot_id, machine_name, history=None, cohort=None,
                kpi=None):
    """The fab as it stands: one record per lot in WIP, one per tool.

    This is what fixes cold start. A mirror rebuilt from the event stream alone
    only learns a lot exists when that lot next moves, and the lots loaded from
    WIP.txt are never released at all -- so at any start point, the dashboard
    under-reports WIP for as long as it takes every lot to touch a tool. A
    snapshot states the position of everything at once.

    Deliberately positionless about time: the consumer applies it as "this is
    now", not as history to replay.
    """
    # A lot is on a tool iff a LotDoneEvent for it is still pending. The sim
    # keeps no "currently processing" list -- that fact lives only in the event
    # queue, so this reads it there rather than inventing a parallel one that
    # could drift.
    running = {}
    for ev in getattr(instance.events, 'arr', []) or []:
        lots_on = getattr(ev, 'lots', None)
        machines_on = getattr(ev, 'machines', None)
        if not lots_on or not machines_on:
            continue
        tool = machine_name(machines_on[0])
        for lot in lots_on:
            running[lot_id(lot)] = tool

    lots = []
    for lot in instance.active_lots:
        lid = lot_id(lot)
        step = getattr(lot, 'actual_step', None)
        lots.append({
            'lot': lid,
            'product': lot.name,
            # lot.name is the order stream ("Lot_9", "HotLot_9"); part_name is
            # the product ("part_9"). The burndown groups cohorts by product,
            # so it needs the latter -- carrying only `product` here made a
            # snapshot lot's part disagree with its own cohort id.
            'part': getattr(lot, 'part_name', '') or lot.name,
            'step': getattr(step, 'step_name', '') if step else '',
            'fam': getattr(step, 'family', '') if step else '',
            'setup': getattr(step, 'setup_needed', '') if step else '',
            'tool': running.get(lid),          # None => waiting, not running
            'done_steps': len(getattr(lot, 'processed_steps', []) or []),
            # The burndown this lot walked during warm-up. Without it an active
            # lot's line can only start at the moment the feed goes live, so
            # every lot appears to have been created at day N with no past --
            # the opposite of what a WIP snapshot is for.
            'hist': decimate(history.get(lid, [])) if history else [],
            # Due date, release and priority: the burndown draws the due
            # marker and the required-rate line from these, and a snapshot
            # lot that lacked them had no deadline on the chart at all.
            'due': round(float(getattr(lot, 'deadline_at', 0) or 0), 1),
            'rel': round(float(getattr(lot, 'release_at', 0) or 0), 1),
            'prio': getattr(lot, 'priority', None),
            'hot': 1 if (getattr(lot, 'priority', 0) or 0) >= 20
            or lot.name.startswith('HotLot') else 0,
            # Stored so --snapshot-only can republish it: the cohort is
            # defined from the release time, which the record does not carry.
            'cohort': cohort(lid) if cohort else None,
        })

    # The sim has no broken flag either; a tool being down is expressed by its
    # events being pushed out. Snapshot every tool as online and let the
    # breakdown/PM stream correct it -- a wrong-but-converging roster beats an
    # invented one, and the deltas arrive within the same cycle.
    tools = [{'tool': machine_name(m), 'online': 1} for m in instance.machines]

    return {
        'day': round(instance.current_time / 86400, 4),
        # Where history ends and the live stream begins. The chart draws one
        # side of this line differently from the other, so it has to be a
        # published fact rather than something the browser guesses.
        'warm_t': round(instance.current_time, 1),
        'lots': lots,
        'tools': tools,
        # Fab-level KPI samples taken during warm-up, so the dashboard's KPI
        # charts start at day 0 rather than at the moment the feed went live.
        'kpi': list(kpi or []),
    }


class RunStore:
    """The Postgres run store (ADR 0004): one row per run, its KPI samples,
    and a summary when it ends.

    Best effort by design. The stream is the product; a run store that is
    down must cost a warning, never the feed. Every method swallows its own
    errors after the first and reports once.
    """

    METRICS = (('throughput_day', 'thr'), ('starts_day', 'starts'),
               ('cycle_time_days', 'ct'),
               ('on_time_pct', 'otd'), ('tardiness_days', 'tard'),
               ('wip_lots', 'wip'), ('util_pct', 'util'))

    def __init__(self):
        self.conn = None
        self.run_id = None
        self.failed = False
        self.live = []                    # post-warm-up samples, for the summary

    @staticmethod
    def dsn():
        return (f"host={os.getenv('PGHOST', 'localhost')} "
                f"port={os.getenv('PGPORT', '25432')} "
                f"dbname={os.getenv('PGDATABASE', 'fab')} "
                f"user={os.getenv('PGUSER', 'fab')} "
                f"password={os.getenv('PGPASSWORD', 'fab')} "
                f"connect_timeout=3")

    def _fail(self, what, e):
        if not self.failed:
            print(f'  run store: {what} failed ({e}); continuing without it',
                  file=sys.stderr)
        self.failed = True

    def begin(self, run_key, a, notes=None):
        try:
            import psycopg
            self.conn = psycopg.connect(self.dsn(), autocommit=True)
            try:
                sha = subprocess.check_output(
                    ['git', 'rev-parse', '--short', 'HEAD'], cwd=REPO,
                    stderr=subprocess.DEVNULL, text=True).strip()
            except Exception:
                sha = None
            with self.conn.cursor() as cur:
                cur.execute(
                    'INSERT INTO runs (dataset, seed, dispatcher, batch_strat, '
                    ' days, warmup_days, git_sha, run_key, status, notes) '
                    'VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id',
                    (a.dataset, a.seed, a.dispatcher, a.batch_strat, a.days,
                     a.warmup_days, sha, run_key, 'running', notes))
                self.run_id = cur.fetchone()[0]
            print(f'  run store: run #{self.run_id} ({run_key}) registered',
                  file=sys.stderr)
        except Exception as e:
            self._fail('connect', e)
            self.conn = None

    def samples(self, rows, warmup):
        if not self.conn or not rows:
            return
        if not warmup:
            self.live.extend(rows)
        try:
            with self.conn.cursor() as cur:
                cur.executemany(
                    'INSERT INTO run_kpi_samples (run_id, t, warmup, wip, running,'
                    ' util, thr, ct, otd, tard, dec, opt, wq, wb, wp, starts, wd) '
                    'VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) '
                    'ON CONFLICT (run_id, t) DO NOTHING',
                    [(self.run_id, r['t'], warmup, r['wip'], r['running'],
                      r['util'], r['thr'], r['ct'], r['otd'], r['tard'],
                      r['dec'], r['opt'], r.get('wq', 0), r.get('wb', 0),
                      r.get('wp', 0), r.get('starts', 0), r.get('wd', 0))
                     for r in rows])
        except Exception as e:
            self._fail('sample insert', e)

    def tools(self, rows):
        """Upsert the per-tool outcome for this run (busy %, dispatches, queue)."""
        if not self.conn or not rows:
            return
        try:
            with self.conn.cursor() as cur:
                cur.executemany(
                    'INSERT INTO run_tools (run_id, tool, family, busy_pct, '
                    ' dispatches, queue_avg, queue_max) '
                    'VALUES (%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (run_id, tool) '
                    'DO UPDATE SET busy_pct=EXCLUDED.busy_pct, '
                    ' dispatches=EXCLUDED.dispatches, queue_avg=EXCLUDED.queue_avg, '
                    ' queue_max=EXCLUDED.queue_max',
                    [(self.run_id, *r) for r in rows])
        except Exception as e:
            self._fail('tool upsert', e)

    def finish(self, status):
        """Close the run: status, finished_at, and the summary KPIs, each the
        mean of the post-warm-up samples (so a run compares on the part that
        was actually run, not on the shared warm-up)."""
        if not self.conn:
            return
        try:
            rows = self.live
            n = len(rows)
            summary = []
            for metric, key in self.METRICS:
                if n:
                    summary.append((metric, sum(r[key] for r in rows) / n))
            tot = sum(r.get('wq', 0) + r.get('wb', 0) + r.get('wp', 0) + r.get('wd', 0)
                      for r in rows)
            if tot:
                for metric, key in (('queue_share_pct', 'wq'),
                                    ('batch_wait_share_pct', 'wb'),
                                    ('processing_share_pct', 'wp'),
                                    ('delay_share_pct', 'wd')):
                    summary.append((metric, 100.0 * sum(r.get(key, 0) for r in rows) / tot))
            dec = sum(r['dec'] for r in rows)
            summary.append(('optimized_pct',
                            100.0 * sum(r['opt'] for r in rows) / dec if dec else 0.0))
            with self.conn.cursor() as cur:
                cur.execute('UPDATE runs SET status=%s, finished_at=now() '
                            'WHERE id=%s', (status, self.run_id))
                cur.executemany(
                    'INSERT INTO run_kpis (run_id, metric, product, value) '
                    "VALUES (%s,%s,'',%s) ON CONFLICT (run_id, metric, product) "
                    'DO UPDATE SET value=EXCLUDED.value',
                    [(self.run_id, m, v) for m, v in summary])
            print(f'  run store: run #{self.run_id} {status}, '
                  f'{n} live samples summarised', file=sys.stderr)
            self.conn.close()
        except Exception as e:
            self._fail('finish', e)


class FileSink:
    """JSONL to a file the API tails. The line-buffered flush matters: the
    reader is following the file, and buffering would stall the dashboard."""

    def __init__(self, path, truncate):
        self.f = open(path, 'w' if truncate else 'a', buffering=1)
        self.label = path

    def write(self, topic, payload, key=None):
        rec = {'topic': topic, 'payload': payload}
        if key is not None:
            rec['key'] = key
        self.f.write(json.dumps(rec) + '\n')
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
        self.label = brokers
        self.dropped = 0
        self.failed = 0
        self.delivered = 0
        self._brokers = brokers
        self._p = None

    @property
    def p(self):
        """Connect on first write, not at construction.

        A warm-up simulates for minutes while emitting nothing, so there is no
        reason to hold an idle librdkafka producer and its threads open across
        it. That is the whole justification: tidiness, not a bug fix.

        CORRECTION. This was originally committed claiming it fixed warm-ups
        that "died silently at around a minute". They were not dying. `ps` was
        being filtered by a shell hook and reported nothing for a live process,
        so each apparently-dead run was relaunched -- four ended up competing
        for one core, which is what made them look stuck. A foreground run
        settled it: the process was fine and simply unfinished. The change is
        still worth keeping; the crash it claimed to fix never existed.
        """
        if self._p is None:
            self._p = self._connect(self._brokers)
        return self._p

    @staticmethod
    def _connect(brokers):
        from confluent_kafka import Producer
        return Producer({
            'bootstrap.servers': brokers,
            # A dashboard feed must never block the simulation; drop before
            # stalling, and say so at the end rather than silently.
            'queue.buffering.max.messages': 200000,
            'linger.ms': 50,
        })

    def _ack(self, err, _msg):
        if err is None:
            self.delivered += 1
        else:
            self.failed += 1

    def write(self, topic, payload, key=None):
        # The key is what makes compaction work: the state topics keep one
        # record per key, so a keyless write to them would be retained forever
        # as unrelated history instead of superseding the previous state.
        try:
            # payload None is a compaction tombstone: the broker drops the key.
            self.p.produce(topic, None if payload is None else payload.encode('utf-8'),
                           key=None if key is None else key.encode('utf-8'),
                           callback=self._ack)
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
        if self._p is None:
            print('  KAFKA: nothing produced (never connected)', file=sys.stderr)
            return
        remaining = self._p.flush(15)
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

    def __init__(self, sink, speed, tool_filter=None, from_day=None, to_day=None,
                 burndown=True, cohort_mode='product-day'):
        self.sink = sink
        self.speed = speed
        # Playback control, refreshed from CONTROL_FILE at most every
        # CONTROL_POLL_S. base_speed is what --speed asked for; the file
        # overrides it while it exists, so deleting the file restores the
        # command line rather than freezing at the last dashboard setting.
        self.base_speed = speed
        self.paused = False
        self._control_checked = 0.0
        self._control_mtime = None
        self.tool_filter = tool_filter
        # A 730-day run is ~39M events and 4.3 GB. Recording a window instead
        # turns that into megabytes without changing the simulation: the sim
        # still runs from t=0, we just stop writing outside the window.
        self.from_s = None if from_day is None else from_day * 86400
        self.to_s = None if to_day is None else to_day * 86400
        self.last_sim_t = None
        self.emitted = 0
        self.skipped = 0
        self.burndown = burndown
        self.cohort_mode = cohort_mode
        # Last cumulative (queue, batch-wait, processing) seconds per lot, so
        # each burndown event can say what the *preceding* flat segment was
        # spent on. Keyed by lot idx and dropped when the lot completes, so it
        # is bounded by WIP rather than by total lots released.
        self._last_split = {}
        # Tools taken down by a breakdown or a PM, and the simulated time each
        # one comes back. PySCFabSim fires a plugin hook when an outage STARTS
        # but none when it ends -- it just shifts the machine's events forward
        # by the sampled length -- so without this the feed is one-way and the
        # dashboard's tool roster decays monotonically to all-down.
        self._down_until = {}
        # Last (breakdown, PM) seconds seen per machine. BreakdownEvent.handle
        # adds the sampled outage length to these counters *before* calling the
        # plugin, so the delta is the exact length without resampling the
        # distribution (which would give a different number every time).
        self._wear = {}
        # Route length, captured once per lot and reused.
        #
        # It cannot be recomputed per event: on the last step the simulator
        # never appends the final step to processed_steps (the while loop in
        # instance.py exits before doing so), so processed+remaining reads one
        # short exactly at completion. Deriving it each time made the route
        # appear to shrink by 1 on 103 of 2,274 lots -- every completion.
        #
        # Rework does NOT change this number. It moves already-processed steps
        # back onto the front of remaining_steps, so the lot has more steps
        # *left* but the same total route; it has gone back in the line and
        # must redo them. Keeping route fixed is what makes that readable on
        # the chart as a step back up rather than a moving target.
        self._route_len = {}
        # Identity of this simulation run.
        #
        # The burndown topic outlives any one run: restart the feed and Kafka
        # still holds the previous run's points, with the same lot ids on a
        # different timeline. Replayed together they interleave into nonsense --
        # a lot reads as finished at t=773838 and 96 steps from done at
        # t=817550. Stamping the run lets the consumer drop everything from an
        # older one instead of drawing both.
        self.run_id = uuid.uuid4().hex[:8]
        # Warm-up burndown, kept rather than thrown away.
        #
        # Warm-up runs with emission suppressed, so these points are computed
        # and then dropped by _write. Recording them here costs nothing extra
        # and is the only chance to capture them: the simulator does not keep
        # a lot's step history, so once warm-up has passed, that shape is gone
        # for good. Keyed by lot id and dropped when a lot completes, so it is
        # bounded by WIP rather than by every lot ever released.
        self._hist = {}
        # Cohort per lot, recorded as lots are seen. A snapshot record has no
        # release time, and the cohort is defined from it, so it cannot be
        # recomputed at snapshot time.
        self._cohort_by_lot = {}
        self._warm_t = None
        # Fab KPIs, sampled every KPI_SAMPLE_S of simulated time from the
        # books below. Sampled here rather than derived in the mirror so the
        # warm-up and the live run are measured by one definition, which is
        # what makes a later A/B against a different dispatcher honest.
        self._kpi = []              # warm-up samples (published with the snapshot)
        self._done_log = []         # (t, cycle_s, tardy_s) for completions
        self._on_tool = {}          # lot_id -> tool while processing
        self._busy = {}             # tool -> lots on it now
        self._next_kpi_t = None
        self._decisions = []        # (t, optimized) per dispatch decision
        # Where lot time goes: (t, queue_s, batch_wait_s, proc_s) per burndown
        # point, i.e. the segment that just ended. Summed over the trailing
        # day it says what share of cycle time is queueing vs holding for
        # batch partners vs actually processing.
        self._split_log = []
        # Lot releases (starts) by time, for starts/day. Today they are the
        # dataset's schedule verbatim (order.txt via file_instance.py); a
        # release policy would make this the KPI it is judged on.
        self._release_log = []
        # Per-tool books for the run store: dispatches, busy seconds, queue
        # seen at dispatch. busy_since is when the tool's current lot(s)
        # started, so busy time is credited when the tool frees up.
        self._tool_books = {}       # tool -> dict(family, dispatches, busy_s, q_sum, q_max)
        self._busy_since = {}       # tool -> sim time it became busy
        self._next_tools_t = None
        self.rule = 'fifo'          # default dispatching rule name, for src=
        self.store = None           # RunStore, attached by main()

    def _in_window(self):
        t = getattr(self, '_now', None)
        if t is None:
            return True
        if self.from_s is not None and t < self.from_s:
            return False
        if self.to_s is not None and t > self.to_s:
            return False
        return True

    def _write(self, topic, payload, key=None):
        if not self._in_window():
            self.skipped += 1
            return
        self.sink.write(topic, payload, key)
        self.emitted += 1

    def _read_control(self):
        """Pick up speed/pause changes made from the dashboard.

        Poll-throttled and mtime-guarded: at 400x this runs thousands of times
        a second, and a stat() per quarter-second is the whole cost.
        """
        now = time.time()
        if now - self._control_checked < CONTROL_POLL_S:
            return
        self._control_checked = now
        try:
            mtime = os.path.getmtime(CONTROL_FILE)
        except OSError:
            # No file (or it was removed): fall back to the command line.
            if self._control_mtime is not None:
                self._control_mtime = None
                self.speed, self.paused = self.base_speed, False
            return
        if mtime == self._control_mtime:
            return
        self._control_mtime = mtime
        try:
            with open(CONTROL_FILE) as f:
                ctl = json.load(f)
        except (OSError, ValueError):
            return                      # half-written file; try again next poll
        if isinstance(ctl.get('speed'), (int, float)):
            self.speed = float(ctl['speed'])
        self.paused = bool(ctl.get('paused'))

    def _pace(self, sim_t):
        # Warm-up (and anything else before --from-day) is unpaced by
        # definition: nothing is emitted, so there is nothing to pace. The
        # control file is not consulted either -- it carries the *playback*
        # speed the dashboard asked for, and honouring it here paced a 5-day
        # warm-up at 20x, i.e. six wall-clock hours of silence.
        if not self._in_window():
            self.last_sim_t = sim_t
            return
        self._read_control()
        # Pause is not speed 0: speed 0 means unpaced (as fast as possible),
        # so a paused feed has to block here instead of falling through.
        while self.paused:
            time.sleep(CONTROL_POLL_S)
            self._control_checked = 0.0
            self._read_control()
            # Resuming should not replay the wall time spent paused as sim
            # backlog; the next delta is measured from wherever we resume.
            self.last_sim_t = sim_t
            self._anchor = None
        if self.speed <= 0:
            self.last_sim_t = sim_t
            self._anchor = None
            return
        # Pace against an anchor (wall, sim, speed) rather than sleeping per
        # event. A sleep per dispatch overshoots by ~0.1 ms each, and at
        # 1000x events are ~1 ms apart, so that version lost 20-30% of the
        # requested speed while using a fifth of a core. Sleeping only once
        # the lag exceeds a few milliseconds keeps the clock honest at any
        # speed the simulator itself can sustain (~10,000x).
        now = time.monotonic()
        a = getattr(self, '_anchor', None)
        if a is None or a[2] != self.speed or self.last_sim_t is None \
                or sim_t < self.last_sim_t:
            self._anchor = a = (now, sim_t, self.speed)
        target = a[0] + (sim_t - a[1]) / self.speed
        lag = target - now
        if lag > 0.004:
            time.sleep(min(lag, 2.0))
        elif lag < -2.0:
            # Fell more than two seconds behind (a stall, a pause, a debugger):
            # do not try to catch up by racing; re-anchor from here.
            self._anchor = (now, sim_t, self.speed)
        self.last_sim_t = sim_t

    def _recover_due(self, now):
        """Bring back every tool whose outage has elapsed.

        Called from the hooks that already carry a simulated clock, so recovery
        rides the existing event stream rather than needing a timer of its own.
        A tool with no further activity anywhere in the fab therefore recovers
        on the next event from any tool -- close enough for a dashboard, and it
        cannot leave a tool down forever.
        """
        if not self._down_until:
            return
        due = [t for t, at in self._down_until.items() if at <= now]
        for tool in due:
            del self._down_until[tool]
            self._write(TOOL_TOPIC, envelope(
                type='TOOL_STATUS', tool=tool, online=1))

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

    # ------------------------------------------------------------------
    # Cohort burndown
    # ------------------------------------------------------------------
    #
    # "Cohort" is not an SMT2020 concept, so it has to be defined here, and the
    # definition decides what the chart means. The burndown groups lots that
    # "must meet at batch operations", and a furnace batch in SMT2020 requires
    # the same product *and* the same step -- the baseline's own batching key is
    # step_name + '_' + part_name. So a cohort has to be product-scoped.
    #
    #   product-day (default)  one product, one day of releases. LVHM releases
    #                          5.57 lots/product/day and a furnace holds 3-6
    #                          lots, so this is almost exactly one batch worth
    #                          of partners -- the group whose spread actually
    #                          predicts a stall at the next furnace.
    #
    #   release-wave           the lots released at the same instant. In LVHM
    #                          every product releases together every 258.46 min,
    #                          so a wave is 10 lots of 10 different products,
    #                          which can *never* batch with each other. Useful
    #                          for watching identically-released lots diverge;
    #                          misleading if read as batch partners.
    #
    # Release times are exact multiples of the interval (the loader forces
    # first_release = 0 for every stream), so bucketing by day is stable rather
    # than jittering across a boundary.
    def _cohort(self, lot):
        if self.cohort_mode == 'release-wave':
            return f'w{int(lot.release_at // 60)}'
        return f'{lot.part_name}-d{int(lot.release_at // 86400)}'

    def _burn(self, instance, lot, state, reason=None):
        """One point on this lot's burndown line.

        steps_remaining counts route *positions* left, including the step being
        worked. It is deliberately not deduplicated by operation name: a lot
        with 40 to go may visit litho six more times, and collapsing those would
        understate the work left. It is also not monotonic -- rework splices
        already-processed steps back onto the front of remaining_steps
        (instance.py:122), so the line can go back up. That is a real event, not
        a glitch; nothing here clamps it.
        """
        if not self.burndown:
            return
        remaining = len(lot.remaining_steps) + (1 if lot.actual_step is not None else 0)
        done = len(lot.processed_steps)
        route = self._route_len.get(lot.idx)
        if route is None:
            route = self._route_len[lot.idx] = done + remaining

        # Attribute the flat run that preceded this point. The simulator keeps
        # cumulative per-lot totals; the delta since this lot's last event is
        # what that horizontal segment was actually spent on.
        q, b, pr = (lot.waiting_time, lot.waiting_time_batching, lot.processing_time)
        lq, lb, lp = self._last_split.get(lot.idx, (0.0, 0.0, 0.0))
        dq, db, dp = q - lq, b - lb, pr - lp
        self._last_split[lot.idx] = (q, b, pr)
        if dq or db or dp:
            # Time on a Delay_* step is a wait the route prescribes (cure,
            # cool-down), not tool processing; booked to its own bucket.
            if state == 'active' and getattr(self, '_last_step_delay', False):
                self._split_log.append((instance.current_time, dq, db, 0.0, dp))
            else:
                self._split_log.append((instance.current_time, dq, db, dp, 0.0))
        if reason is None:
            # waiting_time_batching is the share of the wait spent holding for
            # batch partners, so it is the one reason we can name from data
            # rather than guess.
            reason = ('cohort' if db > 0 and db >= dq else
                      'queue' if dq > 0 else
                      'proc' if dp > 0 else 'none')

        self._cohort_by_lot[self._lot_id(lot)] = self._cohort(lot)

        if not self._in_window():
            # Suppressed: warm-up. Keep the shape instead of discarding it.
            h = self._hist.setdefault(self._lot_id(lot), [])
            # Cap defensively. A route is at most 583 steps, but rework can add
            # to that, and an unbounded list here would be a slow leak.
            if len(h) < 1500:
                h.append((round(instance.current_time, 1), remaining))
            return

        self._write(BURNDOWN_TOPIC, envelope(
            type='LOT_PROGRESS',
            run=self.run_id,
            lot=self._lot_id(lot),
            cohort=self._cohort(lot),
            part=lot.part_name,
            # The step the lot now waits for. A mirror fed without a
            # snapshot learns ready lots from this record, and the scenario
            # planner keys tool eligibility on it.
            step=getattr(getattr(lot, 'actual_step', None), 'step_name', '') or '',
            # Station family of that step. A lot waits in its family's queue
            # and any machine of the family may take it, so this is what the
            # tool page keys "waiting at this tool" on.
            fam=getattr(getattr(lot, 'actual_step', None), 'family', '') or '',
            t=round(instance.current_time, 1),
            left=remaining,
            idx=done,
            route=route,
            # Lot type. SMT2020 gives hot lots priority 20 against 10 for the
            # rest, and they are released on their own much slower stream, so
            # they see the fab differently: a rate learned from regular lots
            # does not describe them.
            hot=1 if (lot.priority or 0) >= 20 or lot.name.startswith('HotLot') else 0,
            state=state,
            reason=reason,
            # Seconds in the preceding segment, split by cause. Lets the client
            # hatch a flat run without re-deriving anything.
            wq=round(dq, 1), wb=round(db, 1), wp=round(dp, 1),
            # Remaining theoretical *process* time. Queue time is not included
            # -- the simulator does not forecast it -- so this is a floor on
            # what is left, not a predicted finish.
            rem_s=round(lot.remaining_time, 1) if lot.actual_step is not None else 0,
            due=round(lot.deadline_at, 1),
            rel=round(lot.release_at, 1),
            prio=lot.priority))

    # ------------------------------------------------------------------
    # Fab KPIs
    # ------------------------------------------------------------------
    def _kpi_sample(self, instance, t):
        """One row of fab KPIs at simulated time t, over a trailing day."""
        lo = t - KPI_WINDOW_S
        self._done_log = [d for d in self._done_log if d[0] > lo]
        self._decisions = [d for d in self._decisions if d[0] > lo]
        self._split_log = [d for d in self._split_log if d[0] > lo]
        self._release_log = [r for r in self._release_log if r > lo]
        n = len(self._done_log)
        cyc = [d[1] for d in self._done_log]
        late = [d[2] for d in self._done_log if d[2] > 0]
        # Delay_* is a 400-station pseudo-toolset for fixed waits (ADR 0008):
        # not capacity, so neither in the utilization denominator nor the
        # numerator, and not in the tool books.
        real = getattr(self, '_real_tools', None)
        if real is None:
            real = self._real_tools = sum(
                1 for m in instance.machines
                if not self._name(m).startswith('Delay_'))
        ntools = real or 1
        busy = sum(1 for t in self._busy if not t.startswith('Delay_'))
        return {
            't': round(t, 1),
            'wip': len(instance.active_lots),
            'running': len(self._on_tool),
            'util': round(100.0 * busy / ntools, 1),
            'thr': n,                                   # lots completed / day
            'ct': round(sum(cyc) / n / 86400, 3) if n else 0,      # days
            'otd': round(100.0 * (n - len(late)) / n, 1) if n else 0,
            'tard': round(sum(late) / len(late) / 86400, 3) if late else 0,
            'dec': len(self._decisions),
            'opt': sum(1 for d in self._decisions if d[1]),
            # lot-hours in the trailing day, by what the lot was doing
            'wq': round(sum(d[1] for d in self._split_log) / 3600, 1),
            'wb': round(sum(d[2] for d in self._split_log) / 3600, 1),
            'wp': round(sum(d[3] for d in self._split_log) / 3600, 1),
            'starts': len(self._release_log),           # lots released / day
            # d[4] is absent on tuples restored from a checkpoint that
            # predates the delay bucket.
            'wd': round(sum(d[4] for d in self._split_log if len(d) > 4) / 3600, 1),
        }

    def _tick_kpi(self, instance):
        """Take every hourly sample the clock has passed since the last one."""
        now = instance.current_time
        if self._next_kpi_t is None:
            self._next_kpi_t = (now // KPI_SAMPLE_S) * KPI_SAMPLE_S + KPI_SAMPLE_S
        while now >= self._next_kpi_t:
            row = self._kpi_sample(instance, self._next_kpi_t)
            self._next_kpi_t += KPI_SAMPLE_S
            if not self._in_window():
                self._kpi.append(row)         # warm-up: kept for the snapshot
                continue
            self._write(KPI_STATE_TOPIC,
                        envelope(type='KPI', run=self.run_id, **row),
                        key=f'{self.run_id}:{int(row["t"])}')
            if self.store is not None:
                self.store.samples([row], warmup=False)

    def emit_kpi_history(self, samples):
        """Publish the warm-up KPI series as one keyed record."""
        enc = ','.join(':'.join(str(r.get(k, 0)) for k in KPI_FIELDS)
                       for r in samples)
        self.sink.write(KPI_STATE_TOPIC, envelope(
            type='KPI_HIST', run=self.run_id, warm_t=self._warm_t,
            fields=':'.join(KPI_FIELDS), n=len(samples), samples=enc or None),
            key=f'{self.run_id}:hist')
        return 1

    def _lot_off_tool(self, instance, lot):
        tool = self._on_tool.pop(self._lot_id(lot), None)
        if tool is None:
            return
        left = self._busy.get(tool, 0) - 1
        if left > 0:
            self._busy[tool] = left
            return
        self._busy.pop(tool, None)
        since = self._busy_since.pop(tool, None)
        tb = self._tool_books.get(tool)
        start = getattr(self, '_tools_from', None)
        # Credit only the streamed span: a tool already busy at the warm-up
        # line has a `since` before it, and counting from there read 200%.
        if tb is not None and since is not None and start is not None:
            tb['busy_s'] += max(0.0, instance.current_time - max(since, start))

    def _tick_tools(self, instance):
        """Write the per-tool books to the run store every six simulated
        hours, so a run still streaming has a busiest-tools view too."""
        if self.store is None or not self._in_window():
            return
        now = instance.current_time
        if self._next_tools_t is None:
            self._next_tools_t = now + TOOLS_FLUSH_S
            self._tools_from = now
            for tb in self._tool_books.values():   # nothing before the span counts
                tb['busy_s'] = 0.0
            return
        if now < self._next_tools_t:
            return
        self._next_tools_t = now + TOOLS_FLUSH_S
        self.flush_tools(now)

    def flush_tools(self, now):
        if self.store is None:
            return
        start = getattr(self, '_tools_from', None)
        if start is None:
            return
        span = max(1.0, now - start)
        rows = []
        for tool, tb in self._tool_books.items():
            if tool.startswith('Delay_'):     # books restored from an older checkpoint
                continue
            busy = tb['busy_s']
            since = self._busy_since.get(tool)
            if since is not None:            # still busy: credit up to now
                busy += max(0.0, now - max(since, start))
            rows.append((tool, tb['family'], 100.0 * busy / span, tb['dispatches'],
                         (tb['q_sum'] / tb['dispatches']) if tb['dispatches'] else 0.0,
                         tb['q_max']))
        self.store.tools(rows)

    def on_step_done(self, instance, lot, step):
        self._now = instance.current_time
        self._recover_due(instance.current_time)
        self._last_step_delay = str(getattr(step, 'family', '')).startswith('Delay')
        self._lot_off_tool(instance, lot)
        self._tick_kpi(instance)
        self._burn(instance, lot, 'active')

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
        self._recover_due(instance.current_time)
        if self.tool_filter and not self._name(machine).startswith(self.tool_filter):
            return
        self._pace(instance.current_time)
        tool = self._name(machine)
        # Who made this decision. The baseline rule is the default; a
        # dispatcher running inside the simulator (ADR 0002) stamps
        # instance.dispatch_source before calling instance.dispatch, and
        # anything other than the default counts as an optimised decision.
        src = getattr(instance, 'dispatch_source', None) or f'rule:{self.rule}'
        optimized = not src.startswith('rule:')
        # Delay_* is a 400-station pseudo-toolset standing in for a fixed wait
        # (docs/adr/0008); nothing is chosen there, so it is not a decision.
        if not tool.startswith('Delay_'):
            self._decisions.append((instance.current_time, optimized))
        for lot in lots:
            self._on_tool[self._lot_id(lot)] = tool
            # t/end are the simulated start and finish of this run on the
            # tool (lot_end_time is absolute and already includes setup),
            # so a consumer can count down what is left without knowing the
            # sampled process time. Additive; older consumers ignore them.
            self._write(LOT_TOPIC, envelope(
                type='LOT_STARTED', lot=self._lot_id(lot), tool=tool,
                recipe=lot.actual_step.step_name, prio=1,
                t=round(instance.current_time, 1),
                end=round(lot_end_time, 1)))
        if tool not in self._busy:
            self._busy_since[tool] = instance.current_time
        self._busy[tool] = self._busy.get(tool, 0) + len(lots)
        tb = self._tool_books.get(tool)
        if tb is None and not tool.startswith('Delay_'):
            tb = self._tool_books[tool] = {
                'family': getattr(machine, 'family', None), 'dispatches': 0,
                'busy_s': 0.0, 'q_sum': 0, 'q_max': 0, 'since': instance.current_time}
        if tb is not None and self._in_window():
            q = len(machine.waiting_lots)
            tb['dispatches'] += 1
            tb['q_sum'] += q
            tb['q_max'] = max(tb['q_max'], q)
        self._tick_kpi(instance)
        self._tick_tools(instance)
        # run stamps every stream, not just burndown. A consumer that cannot
        # tell which run an event came from cannot notice that it is stitching
        # two timelines together -- which is exactly what happened: state from
        # day 5 drawn against decisions from day 30, silently.
        # `queue` is the queue LEFT BEHIND: instance.dispatch() calls
        # reserve_machine_lot() first, which removes the dispatched lots from
        # waiting_lots of every machine they were queued at, and only then
        # calls on_dispatch. So a tool that took the last waiting lot reports
        # queue=0, which reads as "nothing was waiting" when it means "nothing
        # is waiting NOW".
        #
        # The number a dispatching rule is actually judged on is the CHOICE
        # SET -- everything it had to pick from -- so emit that too. It is
        # exact rather than an estimate: reserve() removed precisely these
        # lots from this machine's list.
        #
        # Both are emitted. `queue` keeps its meaning so older rows stay
        # interpretable; `qbefore` is the one to read.
        self._write(DECISION_TOPIC, envelope(
            tool=tool, lots=len(lots), queue=len(machine.waiting_lots),
            qbefore=len(machine.waiting_lots) + len(lots),
            day=round(instance.current_time / 86400, 4),
            run=self.run_id, src=src,
            setup=machine.current_setup or '-'))

    def on_lot_done(self, instance, lot):
        self._now = instance.current_time
        self._recover_due(instance.current_time)
        now = instance.current_time
        self._done_log.append((now, now - float(lot.release_at or 0),
                               now - float(lot.deadline_at or now)))
        self._lot_off_tool(instance, lot)
        self._tick_kpi(instance)
        self._write(LOT_TOPIC, envelope(type='LOT_COMPLETE', lot=self._lot_id(lot)))
        # Tombstone the lot on the compacted state topic. The snapshot put a
        # LOT_STATE record there for every lot in WIP at the warm-up line;
        # without a delete, a mirror that restarts days later bootstraps
        # every lot that has since completed back into WIP and over-reports
        # it by hundreds of lots -- silently, which is the worse failure.
        self._write(LOT_STATE_TOPIC, None, key=self._lot_id(lot))
        self._burn(instance, lot, 'done')
        self._last_split.pop(lot.idx, None)
        self._route_len.pop(lot.idx, None)
        self._hist.pop(self._lot_id(lot), None)

    def on_lots_release(self, instance, lots):
        self._now = instance.current_time
        self._recover_due(instance.current_time)
        # The WIP.txt lots arrive through this hook at t=0: that is the
        # fab's initial state, not a start, and counting it put a 2,400-lot
        # spike at day 0 of the starts series.
        if instance.current_time > 0:
            self._release_log.extend([instance.current_time] * len(lots))
        self._tick_kpi(instance)
        for lot in lots:
            # The route fields (fam/setup/part/batch/proc) are what make the
            # live ready pool plannable by the slate: dispatch/api's
            # /api/slate/plan feeds them straight into libfabslate, and without
            # them the planner has no station family to decompose on and no
            # setup group to cost a changeover against. They are additive, so a
            # consumer that does not know about them is unaffected.
            step = lot.actual_step
            self._write(LOT_TOPIC, envelope(
                type='LOT_READY', lot=self._lot_id(lot),
                recipe=getattr(step, 'step_name', ''),
                prio=1, wafers=25, slack=3600,
                fam=getattr(step, 'family', ''),
                setup=getattr(step, 'setup_needed', '') or '',
                part=lot.part_name,
                bmin=getattr(step, 'batch_min', 1),
                bmax=getattr(step, 'batch_max', 1),
                proc=round(step.processing_time.avg(), 2) if step else 0,
                due=round(lot.deadline_at, 1)))
            # Seed the burndown at full route length, so a lot that has not
            # moved yet still draws a flat line from its release rather than
            # appearing only once it first completes a step.
            self._burn(instance, lot, 'released', reason='none')

    def _cohort_of(self, lot_id):
        """Cohort for a snapshot lot.

        Recorded as each lot is first seen during warm-up, because the snapshot
        record itself carries no release time and the cohort is defined from
        it. Falls back to the product, which still groups lots that could batch
        together even if the day bucket is unknown.
        """
        return self._cohort_by_lot.get(lot_id) or '?'

    def emit_snapshot(self, snap):
        """Publish a snapshot to the compacted state topics, keyed.

        Sent before any deltas so the mirror has the whole fab before the first
        live event lands. Keys are what let compaction collapse this to one
        record per lot; without them the topic would grow forever and a
        bootstrapping consumer would read history instead of state.
        """
        n = 0
        self._warm_t = snap.get('warm_t')
        for L in snap['lots']:
            if self.tool_filter and L['tool'] and \
                    not L['tool'].startswith(self.tool_filter):
                continue
            # History rides on the compacted state topic rather than the
            # burndown stream: compaction keeps exactly one record per lot, so
            # an API starting up later rebuilds the full picture from it, which
            # is the whole point of the snapshot. On the burndown topic it
            # would age out with the deltas.
            hist = ','.join(f'{int(t)}:{v}' for t, v in (L.get('hist') or []))
            self.sink.write(LOT_STATE_TOPIC, envelope(
                type='LOT_STATE', lot=L['lot'], product=L['product'],
                part=L.get('part') or L['product'],
                step=L['step'], fam=L.get('fam') or '', tool=L['tool'],
                done_steps=L['done_steps'],
                run=self.run_id, warm_t=self._warm_t, day=snap['day'],
                cohort=L.get('cohort') or self._cohort_of(L['lot']),
                due=L.get('due'), rel=L.get('rel'), prio=L.get('prio'),
                hot=L.get('hot'), hist=hist or None),
                key=L['lot'])
            n += 1
        for T in snap['tools']:
            if self.tool_filter and not T['tool'].startswith(self.tool_filter):
                continue
            self.sink.write(TOOL_STATE_TOPIC, envelope(
                type='TOOL_STATE', tool=T['tool'], online=T['online'],
                run=self.run_id, day=snap['day']),
                key=T['tool'])
            n += 1
        kpi = snap.get('kpi') or self._kpi
        n += self.emit_kpi_history(kpi)
        if self.store is not None:
            self.store.samples(kpi, warmup=True)
        self.emitted += n
        return n

    def on_breakdown(self, *args):
        self._tool_down(args, kind='breakdown')

    def on_preventive_maintenance(self, *args):
        self._tool_down(args, kind='pm')

    def _outage_length(self, machine, kind):
        """Seconds this outage will last, taken from the simulator's own books.

        BreakdownEvent.handle() samples the length once and adds it to
        machine.bred_time (breakdown) or machine.pmed_time (PM) before calling
        us, so the delta since the last outage on this machine is that exact
        sample. Resampling event.length instead would give a recovery time
        unrelated to the one the simulator is actually running.
        """
        attr = 'bred_time' if kind == 'breakdown' else 'pmed_time'
        total = float(getattr(machine, attr, 0.0) or 0.0)
        key = (id(machine), attr)
        prev = self._wear.get(key, 0.0)
        self._wear[key] = total
        return max(0.0, total - prev)

    def _tool_down(self, args, kind):
        # interface.py declares on_breakdown(machine, event) but events.py
        # calls it as (instance, event) -- accept either rather than trusting
        # the signature.
        machine, now = None, getattr(self, '_now', None)
        for a in args:
            if hasattr(a, 'current_time'):
                now = a.current_time
            if machine is None and hasattr(a, 'machine'):
                machine = a.machine
            elif machine is None and hasattr(a, 'idx') and hasattr(a, 'family'):
                machine = a
        if machine is None:
            return
        tool = self._name(machine)
        if self.tool_filter and not tool.startswith(self.tool_filter):
            return
        length = self._outage_length(machine, kind)
        self._now = now
        self._write(TOOL_TOPIC, envelope(
            type='TOOL_STATUS', tool=tool, online=0, reason=kind,
            down_s=round(length, 1)))
        if now is not None:
            # A second outage while already down extends rather than shortens:
            # whichever end is later is when the tool is actually usable again.
            self._down_until[tool] = max(self._down_until.get(tool, 0.0),
                                         now + length)
        else:
            # No clock to schedule against. Recover on the next event rather
            # than stranding the tool offline for the rest of the run.
            self._down_until[tool] = 0.0


class _CheckpointDone(Exception):
    """--checkpoint-only: the warm-up checkpoint is on disk, nothing else to do."""


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
    sim_runner.add_common_args(p, days_default=5)
    # --dispatcher slate (or slate:none|due|full) runs the CP-SAT planner in
    # place of a sort key. Defaults match bench/tools/compare.py so a live run
    # and a benchmark run are the same rule; 5ms is the measured sweet spot per
    # bench/README, and it is per FAMILY, not per fab.
    p.add_argument('--slate-solver', default='cpsat', choices=['cpsat', 'greedy'])
    p.add_argument('--slate-cycle', type=float, default=60.0,
                   help='slate rebuild cadence, SIMULATED seconds')
    p.add_argument('--slate-budget', type=float, default=0.005,
                   help='per-family solve budget, seconds')
    p.add_argument('--speed', type=float, default=600.0,
                   help='sim-seconds per wall-second; 0 = unpaced')
    p.add_argument('--tool-prefix', default=None,
                   help='only emit for tools whose name starts with this '
                        '(e.g. Litho) -- the full fab is ~53k events/sim-day')
    p.add_argument('--warmup-days', type=float, default=None,
                   help='simulate this many days first (unpaced, silent), '
                        'publish a full WIP snapshot, then stream live from '
                        'there. ~3 min CPU per 30 days; the snapshot is cached')
    p.add_argument('--snapshot-only', action='store_true',
                   help='publish the cached snapshot for --warmup-days and '
                        'exit; no simulation, instant')
    p.add_argument('--no-store', action='store_true',
                   help='do not record this run in the Postgres run store')
    p.add_argument('--notes', default=None,
                   help='free text stored with the run (what this run is for)')
    p.add_argument('--warmup-dispatcher', default=None,
                   help='the rule that runs the warm-up (default: --dispatcher). '
                        'The checkpoint is keyed by THIS rule, so every rule '
                        'under test can take over the same fab at the same '
                        'instant: warm up once under fifo, then fifo, cr and '
                        'slate each diverge from day N. That is the only way '
                        'two live runs are an A/B rather than two histories')
    p.add_argument('--checkpoint-only', action='store_true',
                   help='stop as soon as the --warmup-days checkpoint is '
                        'written (used to build a shared warm-up)')
    p.add_argument('--rebuild', action='store_true',
                   help='ignore a cached simulator checkpoint for --warmup-days '
                        'and re-simulate the warm-up from day 0')
    p.add_argument('--from-day', type=float, default=None,
                   help='only emit from this simulated day onward')
    p.add_argument('--to-day', type=float, default=None,
                   help='stop emitting after this simulated day')
    p.add_argument('--truncate', action='store_true',
                   help='start the feed file fresh')
    p.add_argument('--no-burndown', action='store_true',
                   help='skip per-lot burndown events (roughly halves lot-event '
                        'volume; the lots view goes empty)')
    p.add_argument('--cohort-mode', default='product-day',
                   choices=['product-day', 'release-wave'],
                   help="how lots are grouped into cohorts. product-day groups a "
                        "product's releases by day, which is the set that can "
                        "actually batch together; release-wave groups lots released "
                        "at the same instant, which in LVHM is one lot of each of "
                        "10 products and can never batch (default: product-day)")
    a = p.parse_args()

    a.dataset = sim_runner.normalize_dataset(a.dataset)
    warm_rule = a.warmup_dispatcher or a.dispatcher
    if warm_rule != a.dispatcher and a.warmup_days is None:
        p.error('--warmup-dispatcher needs --warmup-days')
    if (a.from_day is not None and a.to_day is not None
            and a.from_day > a.to_day):
        p.error('--from-day must not exceed --to-day')

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
    if warm_rule != a.dispatcher:
        print(f'  warm-up under {warm_rule}; {a.dispatcher} takes over at '
              f'day {a.warmup_days:g}', file=sys.stderr)
    if a.tool_prefix:
        print(f'  filtered to tools starting with {a.tool_prefix!r}',
              file=sys.stderr)
    if a.from_day is not None or a.to_day is not None:
        print(f'  window: day {a.from_day if a.from_day is not None else 0}'
              f' .. {a.to_day if a.to_day is not None else a.days}',
              file=sys.stderr)

    warm_s = None if a.warmup_days is None else a.warmup_days * 86400
    warmed = warm_s is None
    t_start = time.time()
    # Ten lines over the whole warm-up: frequent enough to see it moving,
    # sparse enough not to become the output.
    report_every = (warm_s / 10) if warm_s else 0
    next_report = report_every
    cpath = cache_path(a.dataset, a.seed, a.dispatcher,
                       a.warmup_days or 0, a.batch_strat)

    if a.snapshot_only:
        if a.warmup_days is None:
            p.error('--snapshot-only needs --warmup-days to know which cache')
        snap = load_snapshot(cpath)
        if snap is None:
            p.error(f'no cached snapshot at {cpath}; run once without '
                    f'--snapshot-only to build it')
        feed = FeedPlugin(out, 0, a.tool_prefix,
                          burndown=not a.no_burndown, cohort_mode=a.cohort_mode)
        n = feed.emit_snapshot(snap)
        out.close()
        print(f'  published cached snapshot: day {snap["day"]}, '
              f'{len(snap["lots"])} lots, {n} records', file=sys.stderr)
        return

    # Suppress emission until the warm-up line, reusing the window machinery:
    # the sim still runs from t=0 (it must -- it is discrete-event), we simply
    # do not publish the first N days.
    from_day = a.warmup_days if warm_s is not None else a.from_day
    speed = 0.0 if warm_s is not None else a.speed
    # Resume from a checkpoint if one covers this run: the warm-up is then
    # paid once per (dataset, seed, dispatcher, batching, day) rather than on
    # every start. --rebuild forces the slow path.
    ckpt = None if (warm_s is None or a.rebuild) else find_ckpt(
        a.dataset, a.seed, warm_rule, a.warmup_days, a.batch_strat, a.days)
    if warm_s and ckpt is None and warm_rule != a.dispatcher:
        # No shared checkpoint yet. Build it under the warm-up rule -- a
        # separate process, so that rule's checkpoint is exactly what a plain
        # `--dispatcher <warm_rule>` run would have produced and resumed from.
        print(f'  no {warm_rule} checkpoint for day {a.warmup_days:g}; '
              f'building it first', file=sys.stderr)
        cmd = [sys.executable, os.path.abspath(__file__),
               '--dataset', a.dataset, '--seed', str(a.seed),
               '--batch-strat', a.batch_strat, '--days', str(a.days),
               '--dispatcher', warm_rule, '--warmup-days', str(a.warmup_days),
               '--checkpoint-only', '--no-store', '--speed', '0',
               '--out', os.devnull]
        env = dict(os.environ, SIM_CONTROL_FILE=os.devnull)
        rc = subprocess.call(cmd, cwd=REPO, env=env)
        ckpt = find_ckpt(a.dataset, a.seed, warm_rule, a.warmup_days,
                         a.batch_strat, a.days)
        if rc != 0 or ckpt is None:
            p.error(f'could not build the {warm_rule} day-{a.warmup_days:g} '
                    'checkpoint')
    if warm_s == 0:
        print('  no warm-up: snapshotting the WIP the dataset ships with, '
              'then streaming', file=sys.stderr)
    elif warm_s is not None and ckpt is None:
        print(f'  warming up to day {a.warmup_days:g} (unpaced, silent) — '
              f'about {a.warmup_days * 3 / 30:.0f} min of CPU',
              file=sys.stderr)

    feed = FeedPlugin(out, speed, a.tool_prefix, from_day, a.to_day,
                      burndown=not a.no_burndown, cohort_mode=a.cohort_mode)
    # base_speed is the post-warm-up rate the dashboard should show and revert
    # to, not the 0 the warm-up runs at.
    feed.base_speed = a.speed
    feed.rule = a.dispatcher
    write_control(a.speed)
    if not a.no_store:
        feed.store = RunStore()
        notes = a.notes
        if warm_rule != a.dispatcher:
            notes = (f'warm-up under {warm_rule} to day {a.warmup_days:g}'
                     + (f'; {a.notes}' if a.notes else ''))
        feed.store.begin(feed.run_id, a, notes=notes)
    # A feed is normally ended with SIGTERM (dev-up.sh --stop, or a kill).
    # Turn that into the Ctrl-C path so the run store still gets a status
    # instead of a row that says "running" forever.
    def _term(*_):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, _term)

    instance = None
    if ckpt is not None:
        t0 = time.time()
        instance = load_checkpoint(ckpt, feed)
        if instance is not None:
            run_to = sim_runner.SECONDS_PER_DAY * a.days
            warmed = True
            feed.from_s = None
            feed.speed = a.speed
            feed._control_checked = 0.0
            feed.last_sim_t = instance.current_time
            snap = snapshot_of(instance, feed._lot_id, feed._name,
                               history=feed._hist, cohort=feed._cohort_of,
                               kpi=feed._kpi)
            save_snapshot(cpath, snap)
            n = feed.emit_snapshot(snap)
            print(f'  resumed from checkpoint at day {snap["day"]} in '
                  f'{time.time() - t0:.1f}s ({os.path.relpath(ckpt, REPO)}): '
                  f'{len(snap["lots"])} lots in WIP, {n} snapshot records '
                  f'published', file=sys.stderr)
    if instance is None:
        instance, run_to = sim_runner.build(
            a.dataset, a.days, a.seed, [feed], a.batch_strat)

    def before_dispatch(instance):
        nonlocal warmed, next_report
        # Warm-up is silent by design -- nothing is emitted before the
        # line -- but silent for tens of minutes is indistinguishable from
        # hung, and that ambiguity has already cost real time: three
        # redundant runs were started because there was no way to tell a
        # working warm-up from a dead one without ps. Report progress
        # against the wall clock, with an ETA derived from the rate
        # actually achieved rather than from an estimate, because the
        # machine is usually shared and the achieved rate is the only
        # honest predictor.
        # warm_s > 0 guards --warmup-days 0, which is a legitimate and
        # useful request -- snapshot the WIP the dataset ships with and
        # stream from there, no warm-up at all -- and which divided by
        # zero here before it ever reached the snapshot.
        if warm_s and not warmed \
                and instance.current_time >= next_report:
            el = time.time() - t_start
            done_frac = instance.current_time / warm_s
            eta = el / done_frac - el if done_frac > 0 else 0
            print(f'    warm-up day {instance.current_time/86400:6.1f}'
                  f' / {a.warmup_days:g}'
                  f'  ({done_frac*100:4.1f}%)'
                  f'  {el/60:5.1f} min elapsed'
                  f'  ~{eta/60:.0f} min left', file=sys.stderr)
            next_report = instance.current_time + report_every

        # Cross the warm-up line exactly once: snapshot the fab, start
        # emitting, and start pacing. Everything before this ran unpaced
        # with emission suppressed, which is why it takes minutes of CPU
        # rather than hours of wall clock.
        if warm_s is not None and not warmed \
                and instance.current_time >= warm_s:
            warmed = True
            snap = snapshot_of(instance, feed._lot_id, feed._name,
                               history=feed._hist, cohort=feed._cohort_of,
                               kpi=feed._kpi)
            save_snapshot(cpath, snap)
            kpath = ckpt_path(a.dataset, a.seed, warm_rule, a.warmup_days,
                              a.batch_strat, a.days)
            try:
                t0 = time.time()
                save_checkpoint(kpath, instance, feed, a.days)
                print(f'  simulator checkpointed to '
                      f'{os.path.relpath(kpath, REPO)} in {time.time() - t0:.1f}s '
                      f'-- later starts resume from here', file=sys.stderr)
            except Exception as e:      # a cache miss next time, not a failure now
                print(f'  checkpoint not saved ({e})', file=sys.stderr)
                if a.checkpoint_only:
                    raise
            if a.checkpoint_only:
                raise _CheckpointDone()
            feed.from_s = None            # stop suppressing
            feed.speed = a.speed          # start pacing
            feed._control_checked = 0.0   # honour any dashboard change
            feed.last_sim_t = instance.current_time
            n = feed.emit_snapshot(snap)
            print(f'  warm-up complete at day {snap["day"]}: '
                  f'{len(snap["lots"])} lots in WIP, {n} snapshot records '
                  f'published (cached to {os.path.relpath(cpath, REPO)})',
                  file=sys.stderr)

    # `slate` is not a sort key in dispatcher_map -- it is the CP-SAT planner
    # (docs/adr/0009), so it has to be constructed against this instance and
    # rebuilt on its own cadence. sim_runner.run accepts the rule object
    # directly, and the slate's rebuild is chained ahead of the feed's own
    # before_dispatch rather than replacing it.
    #
    # This is also what makes `optimized` mean anything on the dashboard:
    # SlateRule stamps instance.dispatch_source per decision, so a decision the
    # solver drove is distinguishable from one its fallback served.
    rule = a.dispatcher
    slate = None
    if isinstance(rule, str) and rule.startswith('slate'):
        import slate_rule
        pressure = rule.split(':', 1)[1] if ':' in rule else 'full'
        slate = slate_rule.SlateRule(
            instance, solver=a.slate_solver, cycle_s=a.slate_cycle,
            budget_s=a.slate_budget, pressure=pressure)
        print(f'  {slate.banner()}', file=sys.stderr)
        if not slate.planner.solver_available:
            print('  WARNING: OR-Tools is not linked, so these are greedy '
                  'decisions wearing cpsat\'s name', file=sys.stderr)
        rule = slate
        feed_before = before_dispatch

        def before_dispatch(inst):          # noqa: F811
            slate.maybe_rebuild(inst)
            feed_before(inst)

    try:
        stopped = sim_runner.run(instance, run_to, rule,
                                 before_dispatch=before_dispatch)
    except _CheckpointDone:
        out.close()
        print('  checkpoint written; --checkpoint-only, stopping here',
              file=sys.stderr)
        return
    if slate is not None:
        print(f'  slate: {slate.stats()}', file=sys.stderr)

    out.close()
    if feed.store is not None:
        feed.flush_tools(instance.current_time)
        feed.store.finish('stopped' if stopped else 'finished')
    msg = (f'  emitted {feed.emitted} events through '
           f'day {instance.current_time/86400:.2f}')
    if feed.skipped:
        msg += f' ({feed.skipped} outside the window)'
    print(msg, file=sys.stderr)


if __name__ == '__main__':
    main()
