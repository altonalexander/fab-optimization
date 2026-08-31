-- Run store. Zone 2 (data).
--
-- This is NOT where live fab state lives. That is Kafka: the compacted
-- fab.lot.state / fab.tool.state topics are the keyed, restart-survivable
-- store the mirror bootstraps from, and duplicating them here would create a
-- second source of truth for the same thing.
--
-- What Postgres is for is the question Kafka is bad at: comparing RUNS.
--   "cycle time under FIFO vs CR vs the dispatcher, same seed"
--   "WIP at day 90 across ten seeds"
--   "which tools were the constraint in run 47"
-- bench/results/ currently answers those with text files, which stops working
-- somewhere around the fifth run.
--
-- Long-format KPIs rather than a wide table: the metric set is not settled
-- yet, and adding a column per metric would mean a migration every time the
-- bench learns to measure something new.

CREATE TABLE IF NOT EXISTS runs (
    id            BIGSERIAL PRIMARY KEY,
    started_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at   TIMESTAMPTZ,
    -- Everything that changes the trajectory. Two runs agreeing on all of
    -- these should produce identical results; if they do not, that is a bug
    -- worth knowing about, and this table is how you would notice.
    dataset       TEXT   NOT NULL,
    seed          INT    NOT NULL,
    dispatcher    TEXT   NOT NULL,
    batch_strat   TEXT   NOT NULL DEFAULT 'Demand',
    days          NUMERIC NOT NULL,
    warmup_days   NUMERIC,
    -- Provenance. A number without the code that produced it is an anecdote.
    git_sha       TEXT,
    solver        TEXT,
    solver_linked BOOLEAN,
    notes         TEXT
);

COMMENT ON COLUMN runs.solver_linked IS
  'Whether the named solver was actually linked. A run that silently fell back '
  'to greedy must not be comparable to one that did not.';

CREATE INDEX IF NOT EXISTS runs_compare_idx
    ON runs (dataset, dispatcher, seed, days);

-- Long format: one row per (run, metric), optionally scoped to a product.
CREATE TABLE IF NOT EXISTS run_kpis (
    run_id   BIGINT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    metric   TEXT   NOT NULL,      -- cycle_time_days, on_time_pct, throughput_day
    product  TEXT   NOT NULL DEFAULT '',   -- '' = whole fab
    value    DOUBLE PRECISION NOT NULL,
    PRIMARY KEY (run_id, metric, product)
);

-- Per-tool outcome, which is what identifies the constraint.
CREATE TABLE IF NOT EXISTS run_tools (
    run_id      BIGINT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    tool        TEXT   NOT NULL,
    family      TEXT,
    busy_pct    DOUBLE PRECISION,
    setup_pct   DOUBLE PRECISION,
    pm_pct      DOUBLE PRECISION,
    down_pct    DOUBLE PRECISION,
    blocked_pct DOUBLE PRECISION,   -- idle WITH lots queued: a real constraint
    starved_pct DOUBLE PRECISION,   -- idle with an empty queue: not one
    dispatches  BIGINT,
    queue_avg   DOUBLE PRECISION,
    queue_max   INT,
    PRIMARY KEY (run_id, tool)
);

CREATE INDEX IF NOT EXISTS run_tools_family_idx ON run_tools (run_id, family);

-- Index of warm-up snapshots, not the snapshots themselves. The blob stays on
-- disk in bench/snapshots/ (and in the compacted topics); this makes them
-- findable and records what they cost, so nobody re-simulates 90 days because
-- they could not remember whether it had been done.
CREATE TABLE IF NOT EXISTS snapshots (
    id           BIGSERIAL PRIMARY KEY,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    dataset      TEXT NOT NULL,
    seed         INT  NOT NULL,
    dispatcher   TEXT NOT NULL,
    batch_strat  TEXT NOT NULL,
    day          NUMERIC NOT NULL,
    lots         INT,
    tools        INT,
    path         TEXT,
    build_secs   DOUBLE PRECISION,
    UNIQUE (dataset, seed, dispatcher, batch_strat, day)
);

-- The producer's run id (the 8-hex `run=` stamp on every Kafka record), so a
-- row here can be matched to the run the live dashboard is showing. NULL for
-- rows written by anything that did not stream.
ALTER TABLE runs ADD COLUMN IF NOT EXISTS run_key TEXT;
-- running | finished | stopped. A run killed mid-way is still worth comparing
-- up to where it got, but must say so.
ALTER TABLE runs ADD COLUMN IF NOT EXISTS status TEXT NOT NULL DEFAULT 'running';
CREATE INDEX IF NOT EXISTS runs_run_key_idx ON runs (run_key);

-- The KPI series behind run_kpis: one row per run per simulated hour, the
-- same samples the producer publishes on fab.kpi.state. This is what lets
-- two runs be compared end to end rather than by their final numbers, and
-- what lets a finished run be laid over the one currently streaming.
CREATE TABLE IF NOT EXISTS run_kpi_samples (
    run_id   BIGINT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    t        DOUBLE PRECISION NOT NULL,   -- simulated seconds since day 0
    warmup   BOOLEAN NOT NULL DEFAULT FALSE, -- sampled before the stream began
    wip      INT,
    running  INT,
    util     DOUBLE PRECISION,
    thr      INT,
    ct       DOUBLE PRECISION,
    otd      DOUBLE PRECISION,
    tard     DOUBLE PRECISION,
    dec      INT,
    opt      INT,
    PRIMARY KEY (run_id, t)
);

-- Lot-hours in the trailing day by what the lot was doing: queueing for a
-- tool, holding for batch partners, processing. Their shares are where cycle
-- time goes.
ALTER TABLE run_kpi_samples ADD COLUMN IF NOT EXISTS wq DOUBLE PRECISION;
ALTER TABLE run_kpi_samples ADD COLUMN IF NOT EXISTS wb DOUBLE PRECISION;
ALTER TABLE run_kpi_samples ADD COLUMN IF NOT EXISTS wp DOUBLE PRECISION;
-- Lots released in the trailing day. Today the dataset's schedule verbatim;
-- the number a release policy would be judged on.
ALTER TABLE run_kpi_samples ADD COLUMN IF NOT EXISTS starts INT;
-- Lot-hours on Delay_* steps (route-prescribed waits: cure, cool-down), kept
-- apart from processing on real tools.
ALTER TABLE run_kpi_samples ADD COLUMN IF NOT EXISTS wd DOUBLE PRECISION;

-- Convenience view: the shape most comparisons want, without re-deriving the
-- pivot each time. Dropped and recreated because Postgres will not reorder or
-- rename a view's columns in place.
DROP VIEW IF EXISTS run_summary;
CREATE VIEW run_summary AS
SELECT r.id, r.dataset, r.dispatcher, r.seed, r.days, r.warmup_days,
       r.batch_strat, r.solver, r.solver_linked, r.started_at, r.finished_at,
       r.run_key, r.status, r.git_sha, r.notes,
       MAX(k.value) FILTER (WHERE k.metric = 'cycle_time_days'
                              AND k.product = '') AS cycle_time_days,
       MAX(k.value) FILTER (WHERE k.metric = 'on_time_pct'
                              AND k.product = '') AS on_time_pct,
       MAX(k.value) FILTER (WHERE k.metric = 'throughput_day'
                              AND k.product = '') AS throughput_day,
       MAX(k.value) FILTER (WHERE k.metric = 'starts_day'
                              AND k.product = '') AS starts_day,
       MAX(k.value) FILTER (WHERE k.metric = 'wip_lots'
                              AND k.product = '') AS wip_lots,
       MAX(k.value) FILTER (WHERE k.metric = 'util_pct'
                              AND k.product = '') AS util_pct,
       MAX(k.value) FILTER (WHERE k.metric = 'tardiness_days'
                              AND k.product = '') AS tardiness_days,
       MAX(k.value) FILTER (WHERE k.metric = 'optimized_pct'
                              AND k.product = '') AS optimized_pct,
       MAX(k.value) FILTER (WHERE k.metric = 'queue_share_pct'
                              AND k.product = '') AS queue_share_pct,
       MAX(k.value) FILTER (WHERE k.metric = 'batch_wait_share_pct'
                              AND k.product = '') AS batch_wait_share_pct,
       MAX(k.value) FILTER (WHERE k.metric = 'processing_share_pct'
                              AND k.product = '') AS processing_share_pct,
       MAX(k.value) FILTER (WHERE k.metric = 'delay_share_pct'
                              AND k.product = '') AS delay_share_pct,
       (SELECT MAX(s.t) FROM run_kpi_samples s WHERE s.run_id = r.id) AS last_t
FROM runs r
LEFT JOIN run_kpis k ON k.run_id = r.id
GROUP BY r.id;
