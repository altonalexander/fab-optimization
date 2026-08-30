# 0004 — Kafka holds live state, Postgres holds runs

**Status:** Accepted, 2026-08-30. Postgres is provisioned and schema-applied;
nothing writes to it yet.

---

## 1. The question

Once cold start needed a durable place to keep fab state (0003), and the bench
needed somewhere to keep results, the obvious move was to add a database and
put both in it. This records why that would have been wrong, and where the line
actually falls.

## 2. Options considered

**Prometheus for everything.** Rejected for state. A WIP snapshot is ~2,000 lot
records with categorical fields — lot id, product, current step, tool. In
Prometheus each lot id becomes a label value, which is textbook cardinality
explosion, and it still could not answer *"what is the state of lot X"* because
it is not a keyed store. It also downsamples and expires, which is exactly
wrong for state that must be exact.

It remains the right tool for aggregate time series — total WIP, per-family
queue depth, throughput, utilisation — and Grafana comes free with it. That is
a metrics pipeline, not a cache, and it is additive whenever someone wants it.

**Postgres for everything, including live state.** Rejected. Kafka's compacted
topics already are a durable, keyed, restart-survivable store, and the mirror
already reads them. Putting state in Postgres as well means the same fact has
two homes, an API dependency on a database it does not otherwise need, and two
things to reconcile when they disagree — which they will.

**Files for run results.** This is the status quo (`bench/results/*.txt`) and
it stops working somewhere around the fifth run. It cannot answer "cycle time
under FIFO vs CR vs the dispatcher at the same seed" without a human reading
four files.

## 3. The decision

**Kafka is the system of record for live fab state.** Compacted
`fab.lot.state` and `fab.tool.state`, keyed by lot and tool. The dashboard
bootstraps from these and from nothing else.

**Postgres is the run store.** `runs`, `run_kpis`, `run_tools`, `snapshots`,
and a `run_summary` view. It holds no live fab state at all, by design.

Schema lives in `dispatch/infra/postgres-init.sql` and is applied on first
start of an empty volume.

## 4. Shape of the schema, and why

- **`run_kpis` is long format** — one row per (run, metric, product) rather
  than a column per metric. The metric set is not settled; a wide table means a
  migration every time the bench learns to measure something new.
- **`runs.solver_linked` is a column, not a note.** A run that silently fell
  back to greedy must not be comparable to one that did not, and this repo has
  already been burned by exactly that: the backend table exists because an
  unlinked CP-SAT once masqueraded as a tie with greedy.
- **`run_tools` carries the blocked/starved split.** Idle *with* lots queued is
  a constraint; idle with an empty queue is not. Collapsing both into
  "utilisation" hides the only distinction that identifies a bottleneck.
- **`snapshots` is an index, not a blob store.** The warm-up snapshots stay on
  disk in `bench/snapshots/`; this table records that they exist and what they
  cost, so nobody re-simulates 90 days for one already built.

## 5. What this assumes

- **That runs are the unit of comparison.** True while the question is "which
  dispatcher is better". It would be wrong if the interesting comparison became
  within-run — a scenario switching policy mid-flight, say — which the schema
  does not model.
- **That live state is never worth querying relationally.** Today the only
  consumer is the dashboard, which wants current position. "Which lots have
  been waiting more than 8 hours at litho" is a plausible future question that
  Kafka answers badly. If that arrives, the answer is a projection *derived
  from* the topics into Postgres — a read model, explicitly downstream — not
  moving the system of record.
- **That losing Postgres is survivable.** It is: the fab still runs, the
  dashboard still works, and only run history is lost. That asymmetry is the
  test of whether the line is drawn in the right place.

## 6. How to know whether it is right

- **It is wrong if** the API ever needs Postgres to serve `/api/state`. That
  would mean live state leaked into the run store.
- **It is wrong if** run comparison starts requiring a join against Kafka. That
  would mean run results leaked into the event stream.
- **It is right if** the first real comparison — 0002's experiment — can be
  expressed as a single query against `run_summary`.

The third is the one that matters, and it has not been done yet, because
nothing writes to these tables so far. This ADR is provisioning ahead of use,
which is worth stating plainly: the schema is a hypothesis about what the bench
will need, and it should be expected to change once the bench actually writes
to it.

## 7. Consequences

- `dev-up.sh` brings Postgres up with Kafka and passes `PG*` to the API.
- `postgres-init.sql` runs only on an empty data directory. Editing it later
  does nothing until the volume is dropped. That is a migration, and it should
  be a deliberate one — noted in the compose file so the next person does not
  lose an hour to it.
- Reaching either store from the host requires the dev override, because
  `data-net` is `internal: true`. See 0005 §consequences.
