"""
publish_runs -- put a compare.py result into the run store the dashboard reads.

`compare.py` writes a JSON file. The dashboard's Results page reads Postgres
(`runs`, `run_kpis`, `run_kpi_samples`, surfaced through the `run_summary`
view and /api/runs). This is the bridge, and it is a SEPARATE script on
purpose: compare.py runs under the simulator's venv, which has no database
driver, while this runs under the API's venv, which does. Neither grows a
dependency it does not need.

    dispatch/api/.venv/bin/python3 bench/tools/publish_runs.py \
        bench/results/compare_SMT2020_LVHM_seed0_30d.json

Provenance is not optional here. The `runs` table has git_sha, solver and
solver_linked columns because, as its own comment says, "a number without the
code that produced it is an anecdote" -- and a run that silently fell back to
greedy must not be comparable to one that did not. All three are filled from
the result file, and publishing refuses if the rule claimed a solver that was
not linked.

Re-publishing the same file is idempotent: a run is keyed on
(dataset, dispatcher, seed, days, run_key) and replaced rather than duplicated,
so iterating on a comparison does not litter the results page.
"""
import argparse
import hashlib
import json
import os
import subprocess
import sys

DSN = os.getenv(
    "PGDSN", "host=localhost port=25432 dbname=fab user=fab password=fab")


def git_sha(repo):
    try:
        return subprocess.run(["git", "-C", repo, "rev-parse", "--short", "HEAD"],
                              capture_output=True, text=True,
                              timeout=10).stdout.strip() or None
    except Exception:
        return None


def run_key(payload, row):
    """Stable id for this (comparison, rule) pair.

    Includes the parameters that change the trajectory, so re-running the same
    comparison replaces its rows while a different cycle or budget lands beside
    them instead of overwriting them.
    """
    h = hashlib.blake2b(digest_size=4)
    h.update(json.dumps([
        payload["dataset"], payload["seed"], payload["days"],
        payload["batch_strat"], payload.get("cycle_s"),
        payload.get("budget_s"), row["rule"],
    ], sort_keys=True).encode())
    return h.hexdigest()


# compare.py's KPI names -> the metric names run_summary pivots on.
METRICS = {
    "cycle_time_days": "cycle_time_days",
    "on_time_pct": "on_time_pct",
    "tardiness_lot_days": "tardiness_days",
}


def publish(payload, path, dry_run=False):
    import psycopg

    repo = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    sha = git_sha(repo)
    days = payload["days"]
    # compare.py only applies the warm-up reset past 365 days; below that the
    # numbers include the fill-up transient and the page must not imply
    # otherwise.
    warmup_days = 365 if days > 365 else 0

    published = []
    with psycopg.connect(DSN) as conn, conn.cursor() as cur:
        for row in payload["rows"]:
            detail = row.get("detail") or {}
            solver = detail.get("solver")
            linked = detail.get("solver_available")
            if solver and solver != "greedy" and linked is False:
                raise SystemExit(
                    f"refusing to publish '{row['rule']}': it names solver "
                    f"'{solver}' but the library reported it NOT linked, so the "
                    "numbers are greedy's wearing another name")

            key = run_key(payload, row)
            notes = (f"compare.py {os.path.basename(path)}; "
                     f"decisions={row.get('decisions')}; "
                     f"fp={row.get('fingerprint')}")
            if "coverage" in detail:
                notes += f"; coverage={detail['coverage']}"
            if detail.get("cycle_s"):
                notes += f"; slate_cycle_s={detail['cycle_s']}"

            if dry_run:
                published.append((f"key:{key}", row["rule"],
                                  len(row.get("samples") or [])))
                continue

            # Replace rather than duplicate. ON DELETE CASCADE clears the
            # child tables, so an edited comparison does not leave a stale
            # series attached to a fresh summary.
            cur.execute("DELETE FROM runs WHERE run_key = %s", (key,))
            cur.execute(
                "INSERT INTO runs (dataset, seed, dispatcher, batch_strat, days,"
                " warmup_days, git_sha, solver, solver_linked, notes, run_key,"
                " status, finished_at)"
                " VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'finished', now())"
                " RETURNING id",
                (payload["dataset"], payload["seed"], row["rule"],
                 payload["batch_strat"], days, warmup_days, sha,
                 solver, linked, notes, key))
            run_id = cur.fetchone()[0]

            for src, metric in METRICS.items():
                if row.get(src) is None:
                    continue
                cur.execute(
                    "INSERT INTO run_kpis (run_id, metric, product, value)"
                    " VALUES (%s,%s,'',%s)", (run_id, metric, float(row[src])))
            # throughput_day makes rows at different horizons comparable;
            # the raw count does not.
            if row.get("throughput") is not None and days:
                cur.execute(
                    "INSERT INTO run_kpis (run_id, metric, product, value)"
                    " VALUES (%s,'throughput_day','',%s)",
                    (run_id, float(row["throughput"]) / float(days)))
            # The share of decisions the optimizer actually made. For the
            # sort-key rules this is 0, which is the honest value: it is what
            # separates a solver-driven row from one that fell back.
            cov = detail.get("coverage")
            cur.execute(
                "INSERT INTO run_kpis (run_id, metric, product, value)"
                " VALUES (%s,'optimized_pct','',%s)",
                (run_id, 100.0 * float(cov) if cov is not None else 0.0))

            samples = row.get("samples") or []
            for s in samples:
                cur.execute(
                    "INSERT INTO run_kpi_samples"
                    " (run_id, t, warmup, wip, thr, ct, otd, tard, dec, opt)"
                    " VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)"
                    " ON CONFLICT (run_id, t) DO NOTHING",
                    (run_id, s["t"], s["warmup"], s.get("wip"), s.get("thr"),
                     s.get("ct"), s.get("otd"), s.get("tard"), s.get("dec"),
                     s.get("opt")))
            published.append((run_id, row["rule"], len(samples)))
        conn.commit()
    return published


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("result", help="a compare_*.json written by compare.py")
    p.add_argument("--dry-run", action="store_true")
    a = p.parse_args()

    with open(a.result) as f:
        payload = json.load(f)

    rows = publish(payload, a.result, dry_run=a.dry_run)
    verb = "would publish" if a.dry_run else "published"
    print(f"  {verb} {len(rows)} runs from {a.result}")
    for run_id, rule, n in rows:
        print(f"    run {run_id}  {rule}  {n} samples")
    if not a.dry_run:
        print("\n  visible at /api/runs and on the dashboard's Results tab")


if __name__ == "__main__":
    main()
