# Running it

Day-to-day operation: starting a session, the two modes, the results tab and
the per-tool probe. For what the project is, see the [README](../README.md);
for the toolchain, [`BUILD.md`](../BUILD.md).

### Start a fresh session

One command. It brings up Kafka and Postgres, starts the API and the UI, waits
for all three to answer, and starts a producer:

```bash
scripts/dev-up.sh --fresh --feed
```

Then open http://localhost:5173/.

| flag | what it does |
|---|---|
| `--feed` | start the simulator producer. Without it the dashboard is empty: the API consumer starts at `latest` and there is nothing to consume. |
| `--fresh` | drop the Kafka and Postgres volumes first, so no snapshot from an earlier run is bootstrapped. This is what "clean start" means here. |
| `--status` | what is listening |
| `--stop` | stop what the script started (it refuses to kill anything it did not) |

`FEED_DAYS` / `FEED_WARMUP` / `FEED_SPEED` / `FEED_RULE` / `FEED_WARMUP_RULE`
override the producer's defaults — 180 simulated days, a 90-day warm-up (so
the dashboard opens on a fab with a full quarter of history and steady-state
KPIs to compare against), 20x realtime, `fifo`, warm-up under `fifo`. The
first start simulates the warm-up (~10 min of CPU); later starts resume from
its checkpoint in under a second. `FEED_RULE=slate FEED_SPEED=1600
scripts/dev-up.sh --feed` is the dispatcher under test taking over the same
fab at day 90.

Run it directly rather than through a pipe; see the note at the top of the
script.

**Why one command rather than three terminals.** `--feed` starts a *single*
producer that publishes the WIP snapshot and then streams from the same point.
Two processes would mean two producer run ids, and the dashboard would be
drawing a snapshot from one run against a live stream from another — which it
will now tell you about (the header badge goes red), but is better not to do.
See [`docs/adr/0003`](adr/0003-cold-start-snapshot-and-delta.md).

### Runs and the results tab

Every `sim_feed.py` run also records itself in the **Postgres run store**
(ADR 0004): a `runs` row at start (dataset, seed, dispatcher, batching,
horizon, warm-up, git sha, the Kafka `run` key), every hourly KPI sample in
`run_kpi_samples` as it is taken, and on exit a status (`finished`, or
`stopped` for Ctrl-C/SIGTERM) plus post-warm-up means in `run_kpis`.
`--no-store` opts out; `--notes` says what a run was for.

The **results** tab reads that store (`/api/runs`, `/api/runs/<id>/kpi`):
a table of every run with its means and deltas against a chosen baseline,
and per-KPI charts laying the selected runs over each other — including the
run currently streaming, whose line grows live. SMT2020 has several
out-of-the-box rules (`fifo`, `cr`, two `lifo`s, `random`) and four batching
strategies, so "baseline" is a choice, not a property; the default is the
oldest finished fifo run.

To record a baseline without disturbing the live dashboard, run the feed
headless into a file, with its own control file so it cannot change the
live feed's pacing:

```bash
SIM_CONTROL_FILE=/tmp/ctl.json baselines/pyscfabsim/.venv/bin/python3 \
    bench/tools/sim_feed.py --days 120 --warmup-days 90 --speed 0 \
    --dispatcher cr --warmup-dispatcher fifo \
    --out /tmp/cr.jsonl --truncate --notes "cr baseline"
```

With `--warmup-dispatcher fifo` every baseline resumes the same day-90
checkpoint the live run did, so the rows on the Results tab differ only in
the rule. Without it, the first run of a new dispatcher pays its own 90-day
warm-up and the rows compare two histories.

### Watching it

The header badge is the simulated fab clock. It shows the current sim day, the
producer run id, and turns red if the snapshot and the live stream are from
different runs. Clicking it returns to the live view.

`--speed` is sim-seconds per wall-second: `1` is realtime, `20` (the default)
is twenty times realtime, `0` is unpaced. The dashboard's menu goes to
1600x; measured, the feed holds the requested rate within 3% to 2000x and
the simulator itself tops out near 10,000x. Playback is the only throttle in
the pipeline, and [`docs/adr/0007`](adr/0007-playback-is-a-cursor-not-a-throttle.md)
is the case for making it a viewer-side cursor over the recorded stream
rather than a producer-side sleep. The dashboard's playback menu changes
it live, and that setting persists in `bench/.sim_control.json` — **if the
clock is not moving, check there first**: a leftover `"paused": true` from an
earlier session leaves the feed running but silent.

### The two modes

The simulator runs in two modes — the same run loop
(`bench/tools/sim_runner.py`) with a different plugin riding it.

*Mode 1 — headless.* No broker, no feed, no pacing, as fast as possible. This
is what you use for KPIs and parameter tuning:

```bash
baselines/pyscfabsim/.venv/bin/python3 bench/tools/tool_probe.py --days 30 --top 15
```

*Mode 2 — producer.* What `--feed` starts. To run it by hand, for a different
dataset or start day:

```bash
baselines/pyscfabsim/.venv/bin/python3 bench/tools/sim_feed.py \
    --days 40 --warmup-days 0 --speed 20
```

`--warmup-days 0` snapshots the WIP the dataset already ships with (~2,200
lots) and streams from there, at no warm-up cost. A later start day has to be
simulated to — roughly 3 minutes of CPU per 30 simulated days on an idle
machine, considerably more on a busy one — and is cached in `bench/snapshots/`
afterwards, so only the first build of a given day is slow.

**Tests:**

```bash
cd dispatch && make test        # 56/56, the C++ suite
scripts/smoke.sh                # end-to-end: API, producer, floorplan, scenario
python3 bench/tools/t_sim_runner.py   # the shared dispatch loop, any interpreter
```

`smoke.sh` runs on :8111 so it can stand beside a running dev API. Every
assertion in it corresponds to a bug that actually shipped.

`t_sim_runner.py` is the one test here that needs nothing — no venv, no
dataset, no broker. It fakes the instance to pin the loop in
`bench/tools/sim_runner.py`, which both `tool_probe.py` and `sim_feed.py` run
on, so a break there would take out the measurements and the dashboard feed
together. `smoke.sh` runs it first for that reason.

**The full four-zone stack:**

```bash
cd dispatch
make infra-up     # docker compose, four networks, every service a container

# or one zone at a time — profiles: equipment · realtime · data · enterprise
cd infra && docker compose --profile data up -d
# containers are named fab-<zone>-<service>, e.g. fab-data-kafka

make verify       # zone declarations
make reach        # reachability — proves the isolation is real
make logs
make infra-down
```

This is a different pipeline from `dev-up.sh`, not just the same one in
containers. In dev the API and UI are host processes and the producer is
`sim_feed.py` in the simulator's venv. Here the producer is the `feed`
container (`infra/Dockerfile.feed`): the same `sim_feed.py`, with
`libfabslate.so` built against OR-Tools inside the image, living in the data
zone where a real MES feed would enter. Its first start simulates the 90-day
warm-up (~10 min of CPU) and caches the checkpoint in the `feed-cache`
volume; every later start resumes in seconds. The `dispatcher` container
(`fabdisp`) is a one-shot closed-loop benchmark: it prints its numbers and
exits 0, which is expected — the dashboard's decisions come from the feed's
slate, not from it.

**Deploying it (homelab, public URL):**

```bash
cd dispatch/infra
cp .env.example .env            # POSTGRES_PASSWORD, PUBLIC_URL (per-app settings)
./deploy.sh                     # compose up --build under the shared secrets
make -C .. verify               # passes clean: no dev override, no host ports
```

`deploy.sh` injects secrets that several apps on the box share (Mailgun for
now) from the homelab SOPS store with `sops exec-env`, so they are never
written in plaintext; per-app values stay in `.env`.

`docker-compose.prod.yml` binds the UI to `127.0.0.1:8080` and nothing else,
so the only way in is the reverse proxy or tunnel on the same box. The
included ingress is a Cloudflare Tunnel (`--profile tunnel`), driven from the
command line by `infra/cf-tunnel.sh` with a Cloudflare API token (Account:
Cloudflare Tunnel Edit, Zone: Zone Read + DNS Edit):

```bash
export CLOUDFLARE_API_TOKEN=...          # or: sops exec-env <file> '...'
./cf-tunnel.sh create walden-fab         # tunnel + token -> .env
./cf-tunnel.sh route fab.<your-domain> http://ui:80   # ingress rule + CNAME
docker compose -f docker-compose.yml -f docker-compose.prod.yml \
    --profile all --profile tunnel up -d
./cf-tunnel.sh status
```

Routes are stored at Cloudflare, so another `route` later (a second hostname,
another service on the same box) takes effect without restarting anything.
Basic auth is only meaningful behind the TLS the tunnel provides.

**Demo lifecycle.** A public demo should not run flat out for nobody. With no
dashboard connected for `IDLE_PAUSE_SECONDS` (default 600) the API pauses the
feed. A paused fab greets the next viewer with a modal that explains the
simulation and offers *Resume at 10× speed* (set `AUTO_RESUME_ON_VIEWER=true`
to resume silently instead). The feed runs to
`FEED_DAYS` and then restarts from the day-90 warm-up checkpoint; every open
dashboard gets a modal explaining the jump, and the previous run stays under
Results. A checkpoint is per horizon, so the first start at a new `FEED_DAYS`
re-simulates the warm-up once (~10 min).

**Access gate.** nginx asks the API (`auth_request`) on every request, so one
gate covers the dashboard, every `/api` route and the SSE stream; `/health`
stays open for uptime checks. Visitors sign in at `/login` with a six-character
**access code**, or ask for a **magic link**: they enter an email, the API
mints a code tied to that email and mails it (from `MAIL_FROM` via Mailgun, `MAILGUN_*`)
with a one-click link. Codes are shareable on purpose; every use is recorded.
Sign-ins from `@AUTH_ADMIN_DOMAIN` (frontanalytics.com) get `/admin`: mint
codes with a note, see who used what and when, disable a code. State lives in
the fab Postgres (`access_codes`, `sessions`). The first code has to come from
somewhere: `dispatch/infra/mint-code.sh` mints one from the box itself.

The gate exists because of the POST routes: `/api/scenario` and
`/api/scenario/compare` run the C++ planner (CPU), `/api/sim/control` changes
the playback speed for *everyone* watching, and `/api/chat` calls Gemini on
Vertex on your project's bill. In dev (`dev-up.sh`, no nginx) nothing enforces
it.

**The dispatcher on its own:**

```bash
cd dispatch
make test                       # 56/56, greedy-only build
make hsms-test                  # two processes, real TCP, real HSMS handshake
make bench                      # prints which backends are linked FIRST
./build-ortools/fabtest --bench 5   # CP-SAT vs greedy, if built per BUILD.md
```

**The simulator:**

```bash
cd baselines/pyscfabsim
.venv/bin/python3 main.py       # 730-day greedy run, writes KPIs

# per-tool view (from the repo root)
baselines/pyscfabsim/.venv/bin/python3 bench/tools/tool_probe.py --days 30 --top 15
baselines/pyscfabsim/.venv/bin/python3 bench/tools/tool_probe.py \
    --days 30 --tool 970 --tail 40
```

The probe is headless: it runs the window as fast as it can and reports. To
watch a run unfold, feed the dashboard with `bench/tools/sim_feed.py` and use
its playback controls — that path pages through Kafka's log, so nothing has to
re-simulate to redraw. Recording a bespoke stream instead would be ~2.5 GB per
730-day scenario at 22.5k dispatch events per simulated day, which is the
argument for letting the log be the recording.

