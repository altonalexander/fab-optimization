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

-- Convenience view: the shape most comparisons want, without re-deriving the
-- pivot each time.
CREATE OR REPLACE VIEW run_summary AS
SELECT r.id, r.dataset, r.dispatcher, r.seed, r.days, r.solver,
       r.solver_linked, r.started_at,
       MAX(k.value) FILTER (WHERE k.metric = 'cycle_time_days'
                              AND k.product = '') AS cycle_time_days,
       MAX(k.value) FILTER (WHERE k.metric = 'on_time_pct'
                              AND k.product = '') AS on_time_pct,
       MAX(k.value) FILTER (WHERE k.metric = 'throughput_day'
                              AND k.product = '') AS throughput_day
FROM runs r
LEFT JOIN run_kpis k ON k.run_id = r.id
GROUP BY r.id;
