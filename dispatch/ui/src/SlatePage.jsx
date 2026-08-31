import { useEffect, useState } from 'react'

// Slate — the CP-SAT planner, live, and the head-to-head table it is judged on.
//
// Two questions, deliberately on one page because they are the same claim seen
// from two sides:
//
//   "what would the dispatcher do with the fab as it stands right now?"
//        -> Plan now, against the live ready pool, through the same
//           libfabslate.so the benchmark loads. Not a reimplementation: a
//           dashboard that models the planner separately will eventually
//           disagree with it, and the disagreement is invisible.
//
//   "is it any better than the rules the testbed ships with?"
//        -> the comparison table, written by bench/tools/compare.py on
//           identical demand, seed and horizon.
//
// The solver-not-linked banner is not decoration. Without OR-Tools every
// "cpsat" number is really the greedy fallback wearing its name, and a reader
// has no way to tell from the numbers alone.

function pct(x) { return typeof x === 'number' ? `${(100 * x).toFixed(1)}%` : '–' }

export default function SlatePage() {
  const [status, setStatus] = useState(null)
  const [plan, setPlan] = useState(null)
  const [planning, setPlanning] = useState(false)
  const [runs, setRuns] = useState([])
  const [run, setRun] = useState(null)
  const [err, setErr] = useState(null)

  useEffect(() => {
    let live = true
    fetch('/api/slate/status').then(r => r.json())
      .then(j => live && setStatus(j)).catch(() => {})
    fetch('/api/slate/compare').then(r => r.json())
      .then(j => { if (live) setRuns(j.runs || []) }).catch(() => {})
    return () => { live = false }
  }, [])

  const openRun = (name) => {
    setRun(null)
    fetch(`/api/slate/compare?run=${encodeURIComponent(name)}`)
      .then(r => r.json()).then(setRun).catch(() => {})
  }

  const doPlan = () => {
    setPlanning(true); setErr(null)
    fetch('/api/slate/plan', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ budget_s: 0.25 }),
    })
      .then(async r => {
        const j = await r.json()
        if (!r.ok) throw new Error(j.error || `HTTP ${r.status}`)
        return j
      })
      .then(j => { setPlan(j); setPlanning(false) })
      .catch(e => { setErr(String(e.message || e)); setPlanning(false) })
  }

  return (
    <section>
      <h3>Slate</h3>

      {status && !status.available && (
        <p className="danger">
          Planner unavailable: {status.error || 'unknown'}<br />
          <span className="muted">
            Build it with <code>dispatch/build-slate.sh</code>.
          </span>
        </p>
      )}

      {status?.available && (
        <p className="muted">
          solver <b>{status.solver}</b> over {status.tools} tools
          {!status.solver_linked && (
            <span className="danger">
              {' '}— OR-Tools NOT linked, so these are greedy results wearing
              cpsat&apos;s name. Rebuild before drawing a conclusion.
            </span>
          )}
        </p>
      )}

      <div style={{ margin: '12px 0' }}>
        <button onClick={doPlan} disabled={planning || !status?.available}>
          {planning ? 'planning…' : 'Plan the live pool'}
        </button>
      </div>

      {err && <p className="danger">{err}</p>}

      {plan && (
        <>
          <p className="muted">
            {plan.stats.assigned} of {plan.lots_planned} waiting lots assigned in{' '}
            {(plan.stats.solve_time_s * 1000).toFixed(0)} ms
            {' '}({plan.stats.variables} variables, {plan.stats.status})
            {plan.stats.detail ? ` — ${plan.stats.detail}` : ''}
            {plan.missing_route_fields > 0 && (
              <><br /><span className="danger">
                {plan.missing_route_fields} lots skipped: no station family on the
                event. The feed must emit route fields on LOT_READY.
              </span></>
            )}
          </p>

          <h4>Busiest families</h4>
          <table>
            <thead><tr><th>family</th><th>waiting</th></tr></thead>
            <tbody>
              {plan.families.map(f => (
                <tr key={f.family}><td>{f.family}</td><td>{f.waiting}</td></tr>
              ))}
            </tbody>
          </table>

          <h4>Assignments</h4>
          <table>
            <thead>
              <tr><th>lot</th><th>family</th><th>tool</th>
                  <th>alternate</th><th>rank</th><th>expected</th></tr>
            </thead>
            <tbody>
              {plan.assignments.slice(0, 60).map(a => (
                <tr key={a.lot_id}>
                  <td>{a.lot_id}</td><td>{a.family}</td><td>{a.tool}</td>
                  {/* alternate == tool means no failover target existed: the
                      lot has exactly one eligible machine this cycle. */}
                  <td className={a.alternate === a.tool ? 'muted' : ''}>
                    {a.alternate === a.tool ? 'none' : a.alternate}
                  </td>
                  <td>{a.rank}</td><td>{a.expected_process_s}s</td>
                </tr>
              ))}
            </tbody>
          </table>
          {plan.assignments.length > 60 && (
            <p className="muted">showing 60 of {plan.assignments.length}</p>
          )}
        </>
      )}

      <h4>Head to head</h4>
      {runs.length === 0 && (
        <p className="muted">
          No comparison runs yet. Produce one with{' '}
          <code>python3 bench/tools/compare.py --days 30 --rules cr,slate</code>.
        </p>
      )}
      {runs.length > 0 && (
        <p>
          {runs.map(r => (
            <button key={r.name} onClick={() => openRun(r.name)}
                    style={{ marginRight: 8 }}>
              {r.dataset} · {r.days}d · seed {r.seed}
            </button>
          ))}
        </p>
      )}

      {run && (
        <>
          <p className="muted">
            {run.dataset}, {run.days} days, seed {run.seed}, batch{' '}
            {run.batch_strat}, cycle {run.cycle_s}s — identical demand and
            breakdowns on every row, which is the only reason the rows are
            comparable.
          </p>
          <table>
            <thead>
              <tr><th>rule</th><th>cycle time (d)</th><th>throughput</th>
                  <th>on-time %</th><th>tardiness (lot·d)</th>
                  <th>coverage</th><th>wall</th></tr>
            </thead>
            <tbody>
              {run.rows.map(r => (
                <tr key={r.rule}>
                  <td><b>{r.rule}</b></td>
                  <td>{r.cycle_time_days}</td>
                  <td>{r.throughput}</td>
                  <td>{r.on_time_pct}</td>
                  <td>{r.tardiness_lot_days}</td>
                  {/* Coverage is the share of decision points the slate
                      actually decided. A low number means the row largely
                      measures the fallback, not the slate. */}
                  <td>{pct(r.detail?.coverage)}</td>
                  <td className="muted">{r.wall_s}s</td>
                </tr>
              ))}
            </tbody>
          </table>
          {run.rows.some(r => r.rule === 'slate-cr') && (
            <p className="muted">
              <code>slate-cr</code> routes through the slate call path but
              returns CR&apos;s ordering. It must match <code>cr</code> exactly;
              if it does not, the harness is wrong and no other row means
              anything.
            </p>
          )}
        </>
      )}
    </section>
  )
}
