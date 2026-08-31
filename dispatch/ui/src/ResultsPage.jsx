import { useEffect, useMemo, useState } from 'react'
import { KPIS, Info, valueOf, SPLIT, SPLIT_INFO, splitOf, SplitBar } from './KpiPanel.jsx'

/**
 * Results: every run in the Postgres run store, compared end to end.
 *
 * A run is one (dataset, dispatcher, batching, seed) trajectory recorded by
 * the producer -- the same hourly KPI samples the live tab draws, written to
 * run_kpi_samples as they are taken, plus a summary when the run ends. So a
 * finished run and the one streaming right now are the same kind of thing,
 * and can be laid over each other: the live run's line simply grows.
 *
 * "Baseline" is a choice, not a property: SMT2020 ships several out-of-the-box
 * rules (fifo, cr, lifo, random) and four batching strategies, so the page
 * lets you pick which run the deltas are measured against. Default: the
 * oldest finished fifo run on the same dataset as the live one.
 */

const PALETTE = ['#2563eb', '#111827', '#dc2626', '#059669', '#d97706', '#7c3aed', '#0891b2', '#be185d']

function fmtDay(t) { return t == null ? '—' : `d${(t / 86400).toFixed(1)}` }
function fmtWhen(iso) {
  if (!iso) return '—'
  const d = new Date(iso)
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleString()
}

// Summary columns: the run_summary view's fields, mapped to the KPI list so
// the tile definitions and the deltas use one vocabulary.
const COLS = [
  { key: 'throughput_day', kpi: 'thr', better: 'up' },
  { key: 'starts_day', kpi: 'starts', better: null },
  { key: 'cycle_time_days', kpi: 'ct', better: 'down' },
  { key: 'on_time_pct', kpi: 'otd', better: 'up' },
  { key: 'util_pct', kpi: 'util', better: 'up' },
  { key: 'wip_lots', kpi: 'wip', better: 'down' },
  { key: 'optimized_pct', kpi: 'optpct', better: 'up' },
]

// A run still streaming has no stored summary: derive one from its samples
// so the table and the deltas cover it too.
function summarize(samples) {
  const live = (samples || []).filter(s => !s.warmup)
  if (!live.length) return null
  const mean = k => live.reduce((a, s) => a + Number(s[k] || 0), 0) / live.length
  const dec = live.reduce((a, s) => a + Number(s.dec || 0), 0)
  const opt = live.reduce((a, s) => a + Number(s.opt || 0), 0)
  return {
    throughput_day: mean('thr'), starts_day: mean('starts'), cycle_time_days: mean('ct'), on_time_pct: mean('otd'),
    util_pct: mean('util'), wip_lots: mean('wip'), tardiness_days: mean('tard'),
    optimized_pct: dec ? 100 * opt / dec : 0,
  }
}

// Where cycle time went over a run: the lot-hours summed over its live
// samples, as shares. Same numbers the finished-run summary stores.
function splitOfRun(samples) {
  const live = (samples || []).filter(s => !s.warmup)
  if (!live.length) return null
  const tot = { wq: 0, wb: 0, wp: 0, wd: 0 }
  for (const s of live) for (const k in tot) tot[k] += Number(s[k] || 0)
  return splitOf(tot)
}

function RunDetail({ run, color }) {
  const [tools, setTools] = useState(null)
  useEffect(() => {
    if (!run) return
    let alive = true
    const load = () => fetch(`/api/runs/${run.id}/tools`).then(r => r.json())
      .then(d => alive && setTools(d)).catch(() => {})
    load()
    const id = run.status === 'running' ? setInterval(load, 30000) : null
    return () => { alive = false; if (id) clearInterval(id) }
  }, [run?.id, run?.status])
  if (!run) return null
  const top = (tools?.tools || []).slice(0, 15)
  const fams = (tools?.families || []).slice(0, 12)
  return (
    <div className="side" style={{ marginTop: 6 }}>
      <div className="kpi-card">
        <div className="kpi-head">
          <span className="stat-label">busiest tools <Info text="Share of the run's streamed span each tool had a lot on it, with dispatches and the queue seen at each dispatch. A tool near 100% busy with a long queue is the constraint; the run store refreshes this every six simulated hours for a run still streaming." /></span>
          <span className="muted" style={{ fontSize: 11 }}>
            <span className="swatch" style={{ background: color }} />#{run.id} {run.dispatcher}/{run.batch_strat}
          </span>
        </div>
        {!top.length ? <p className="muted" style={{ fontSize: 11 }}>no tool outcome recorded yet (written every six simulated hours)</p> : (
          <table className="mini">
            <thead><tr><th>tool</th><th className="num">busy</th><th className="num">dispatches</th><th className="num">queue avg</th><th className="num">max</th></tr></thead>
            <tbody>
              {top.map(t => (
                <tr key={t.tool}>
                  <td>{t.tool}</td>
                  <td className="num bar-cell"><span style={{ width: `${Math.min(100, t.busy_pct || 0)}%` }} /><b>{(t.busy_pct || 0).toFixed(0)}%</b></td>
                  <td className="num">{t.dispatches}</td>
                  <td className="num">{(t.queue_avg || 0).toFixed(1)}</td>
                  <td className="num">{t.queue_max}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
      <div className="kpi-card">
        <div className="kpi-head">
          <span className="stat-label">busiest toolsets <Info text="The same, rolled up by tool family (the SMT2020 toolset): mean busy share across the family's tools, total dispatches, and the longest queue any tool in it saw. The family is the capacity unit a fab actually buys." /></span>
          <span className="muted" style={{ fontSize: 11 }}>{tools?.families?.length || 0} families</span>
        </div>
        {!fams.length ? <p className="muted" style={{ fontSize: 11 }}>—</p> : (
          <table className="mini">
            <thead><tr><th>toolset</th><th className="num">tools</th><th className="num">busy</th><th className="num">dispatches</th><th className="num">queue max</th></tr></thead>
            <tbody>
              {fams.map(f => (
                <tr key={f.family}>
                  <td>{f.family}</td>
                  <td className="num">{f.tools}</td>
                  <td className="num bar-cell"><span style={{ width: `${Math.min(100, f.busy_pct || 0)}%` }} /><b>{(f.busy_pct || 0).toFixed(0)}%</b></td>
                  <td className="num">{f.dispatches}</td>
                  <td className="num">{f.queue_max}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  )
}

function Delta({ v, base, better, fmt }) {
  if (v == null || base == null) return <span className="muted">—</span>
  const d = v - base
  if (Math.abs(d) < 1e-9) return <span className="muted">±0</span>
  const good = better === 'up' ? d > 0 : d < 0
  return (
    <span style={{ color: better == null ? '#6b7280' : good ? '#059669' : '#dc2626', fontSize: 11 }}>
      {d > 0 ? '+' : ''}{fmt(d)}
    </span>
  )
}

function MultiChart({ kpi, series, w = 1200, h = 150 }) {
  const M = { l: 44, r: 10, t: 8, b: 22 }
  const iw = w - M.l - M.r, ih = h - M.t - M.b
  let tMax = 86400, yMax = kpi.pct ? 100 : 0
  for (const s of series) {
    for (const p of s.samples) {
      if (p.t > tMax) tMax = p.t
      if (!kpi.pct) { const v = valueOf(kpi, p); if (v != null && v > yMax) yMax = v }
    }
  }
  if (!kpi.pct) yMax = yMax > 0 ? yMax * 1.1 : 1
  tMax *= 1.02
  const x = t => M.l + (t / tMax) * iw
  const y = v => M.t + ih - (Math.min(v, yMax) / yMax) * ih
  const path = pts => pts.map((p, i) => {
    const v = valueOf(kpi, p)
    return `${i ? 'L' : 'M'}${x(p.t).toFixed(1)},${y(v == null ? 0 : v).toFixed(1)}`
  }).join(' ')
  const span = tMax / 86400
  const step = span > 120 ? 30 : span > 40 ? 10 : span > 12 ? 5 : 1
  const ticks = []
  for (let d = 0; d <= span; d += step) ticks.push(d)
  return (
    <svg width="100%" viewBox={`0 0 ${w} ${h}`} preserveAspectRatio="none"
         style={{ display: 'block', height: h }}>
      {[0, 0.5, 1].map(f => (
        <g key={f}>
          <line x1={M.l} x2={w - M.r} y1={y(f * yMax)} y2={y(f * yMax)} stroke="#e5e7eb" />
          <text x={M.l - 4} y={y(f * yMax) + 3} fontSize="9" textAnchor="end" fill="#6b7280">
            {kpi.pct ? Math.round(f * 100) : (f * yMax >= 100 ? Math.round(f * yMax) : (f * yMax).toFixed(1))}
          </text>
        </g>
      ))}
      {ticks.map(d => (
        <text key={d} x={x(d * 86400)} y={h - 6} fontSize="9" textAnchor="middle" fill="#6b7280">d{d}</text>
      ))}
      {series.map(s => {
        // Warm-up drawn faint: shared history, not this run's doing.
        const warm = s.samples.filter(p => p.warmup)
        const liveStart = warm.length ? [warm[warm.length - 1]] : []
        const live = liveStart.concat(s.samples.filter(p => !p.warmup))
        return (
          <g key={s.id}>
            {warm.length > 1 && (
              <path d={path(warm)} fill="none" stroke={s.color} strokeWidth="1" strokeOpacity="0.25" />
            )}
            {live.length > 1 && (
              <path d={path(live)} fill="none" stroke={s.color} strokeWidth={s.live ? 2 : 1.5}
                    strokeDasharray={s.baseline ? undefined : undefined} />
            )}
          </g>
        )
      })}
    </svg>
  )
}

export default function ResultsPage() {
  const [runs, setRuns] = useState(null)
  const [liveKey, setLiveKey] = useState(null)
  const [err, setErr] = useState(null)
  const [series, setSeries] = useState({})        // run id -> samples
  const [baseline, setBaseline] = useState(null)   // run id
  const [selected, setSelected] = useState(null)   // Set of run ids, null = default
  const [detailId, setDetailId] = useState(null)   // run shown in the tool panels

  // The run list, refreshed so a run that just finished gets its summary and
  // a run that just started appears.
  useEffect(() => {
    let alive = true
    const load = () => fetch('/api/runs').then(r => r.json()).then(d => {
      if (!alive) return
      if (d.error) setErr(d.error)
      else setErr(null)
      setRuns(d.runs || [])
      setLiveKey(d.live_run_key || null)
    }).catch(() => alive && setErr('could not load /api/runs'))
    load()
    const id = setInterval(load, 15000)
    return () => { alive = false; clearInterval(id) }
  }, [])

  const liveRun = useMemo(() => runs?.find(r => r.run_key && r.run_key === liveKey) || null, [runs, liveKey])

  // Default baseline: oldest finished fifo run on the live run's dataset
  // (or any dataset), else the oldest finished run, else nothing.
  useEffect(() => {
    if (!runs || baseline != null) return
    const ds = liveRun?.dataset
    const finished = runs.filter(r => r.status === 'finished' && (!liveRun || r.id !== liveRun.id))
    const pick = finished.filter(r => r.dispatcher === 'fifo' && (!ds || r.dataset === ds)).slice(-1)[0]
      || finished.slice(-1)[0]
    if (pick) setBaseline(pick.id)
  }, [runs, liveRun, baseline])

  // Default comparison set: the live run, the baseline, and the newest
  // finished runs up to five lines; the user can change it.
  const shown = useMemo(() => {
    if (!runs) return []
    if (selected) return runs.filter(r => selected.has(r.id))
    const ids = new Set()
    if (liveRun) ids.add(liveRun.id)
    if (baseline != null) ids.add(baseline)
    for (const r of runs) { if (ids.size >= 5) break; if (r.status !== 'running' || r.id === liveRun?.id) ids.add(r.id) }
    return runs.filter(r => ids.has(r.id))
  }, [runs, selected, liveRun, baseline])

  // Series for every shown run. The live run is re-fetched on every tick so
  // its line grows; finished runs are fetched once.
  useEffect(() => {
    let alive = true
    const need = shown.filter(r => r.status === 'running' || !series[r.id])
    if (!need.length) return
    Promise.all(need.map(r => fetch(`/api/runs/${r.id}/kpi`).then(x => x.json())
      .then(d => [r.id, d.samples || []]).catch(() => [r.id, []])))
      .then(pairs => { if (alive) setSeries(s => ({ ...s, ...Object.fromEntries(pairs) })) })
    return () => { alive = false }
  }, [shown, runs])  // eslint-disable-line react-hooks/exhaustive-deps

  if (err && !runs?.length) return <p className="muted">{err}</p>
  if (!runs) return <p className="muted">loading runs…</p>
  if (!runs.length) {
    return (
      <p className="muted">
        No runs recorded yet. Every <code>sim_feed.py</code> run registers itself
        in the Postgres run store and writes its KPI samples as it goes; start one
        (or a headless baseline with <code>--out</code>) and it appears here.
      </p>
    )
  }

  const baseRun = runs.find(r => r.id === baseline) || null
  const summaryOf = r => (r.status === 'finished' && r.throughput_day != null)
    ? r : { ...r, ...(summarize(series[r.id]) || {}) }
  const baseSum = baseRun ? summaryOf(baseRun) : null
  const colorOf = (() => {
    const m = new Map()
    let i = 0
    return r => {
      if (r.id === liveRun?.id) return PALETTE[0]
      if (r.id === baseline) return PALETTE[1]
      if (!m.has(r.id)) m.set(r.id, PALETTE[2 + (i++ % (PALETTE.length - 2))])
      return m.get(r.id)
    }
  })()
  const chartSeries = shown.map(r => ({
    id: r.id, color: colorOf(r), samples: series[r.id] || [],
    live: r.id === liveRun?.id, baseline: r.id === baseline,
  }))
  const toggle = id => {
    const cur = new Set(selected ? selected : shown.map(r => r.id))
    if (cur.has(id)) cur.delete(id); else cur.add(id)
    setSelected(cur)
  }

  return (
    <div>
      {err && <p className="muted" style={{ color: '#b91c1c' }}>{err}</p>}
      <div className="results-toolbar">
        <label>
          baseline{' '}
          <select value={baseline ?? ''} onChange={e => setBaseline(Number(e.target.value) || null)}>
            <option value="">none</option>
            {runs.map(r => (
              <option key={r.id} value={r.id}>
                #{r.id} {r.dispatcher}/{r.batch_strat} seed {r.seed} · {r.status}
              </option>
            ))}
          </select>
        </label>
        <span className="muted" style={{ fontSize: 11 }}>
          deltas in the table are against the baseline; tick runs to add them to the charts
        </span>
      </div>

      <div style={{ overflowX: 'auto' }}>
        <table className="runs">
          <thead>
            <tr>
              <th></th><th>run</th><th>dispatcher</th><th>batching</th><th>seed</th>
              <th>days</th><th>status</th><th>started</th>
              {COLS.map(c => {
                const k = KPIS.find(x => x.key === c.kpi)
                return <th key={c.key}>{k.label} <Info text={k.info} /></th>
              })}
              <th>notes</th>
            </tr>
          </thead>
          <tbody>
            {runs.map(r => {
              const sum = summaryOf(r)
              const isLive = r.id === liveRun?.id
              const isBase = r.id === baseline
              const on = shown.some(x => x.id === r.id)
              return (
                <tr key={r.id} className={isLive ? 'row-live' : isBase ? 'row-base' : ''}>
                  <td><input type="checkbox" checked={on} onChange={() => toggle(r.id)} /></td>
                  <td>
                    <span className="swatch" style={{ background: on ? colorOf(r) : '#e5e7eb' }} />
                    #{r.id}
                    {isLive && <span className="pill pill-live">live</span>}
                    {isBase && <span className="pill">baseline</span>}
                  </td>
                  <td>{r.dispatcher}</td>
                  <td>{r.batch_strat}</td>
                  <td>{r.seed}</td>
                  <td>{r.warmup_days != null ? `${Number(r.warmup_days)}→` : ''}{Number(r.days)}
                      {r.status === 'running' && <span className="muted"> · at {fmtDay(r.last_t)}</span>}</td>
                  <td>{r.status}</td>
                  <td style={{ whiteSpace: 'nowrap' }}>{fmtWhen(r.started_at)}</td>
                  {COLS.map(c => {
                    const k = KPIS.find(x => x.key === c.kpi)
                    const v = sum[c.key]
                    return (
                      <td key={c.key} style={{ whiteSpace: 'nowrap' }}>
                        {v == null ? <span className="muted">—</span> : k.fmt(Number(v))}
                        {baseSum && !isBase && (
                          <> <Delta v={v == null ? null : Number(v)} base={baseSum[c.key] == null ? null : Number(baseSum[c.key])}
                                    better={c.better} fmt={d => k.pct || c.kpi === 'thr' || c.kpi === 'wip' ? (Math.round(d * 10) / 10).toString() : d.toFixed(2)} /></>
                        )}
                      </td>
                    )
                  })}
                  <td className="muted" style={{ maxWidth: 260, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}
                      title={r.notes || ''}>{r.notes || ''}</td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>

      {/* Headline metrics beside where the time went, one row per shown run,
          so the numbers and the reason for them sit side by side. */}
      <h4 style={{ margin: '18px 0 6px' }}>Headline metrics and where cycle time goes</h4>
      <div className="side">
        <div className="kpi-card">
          <div className="kpi-head"><span className="stat-label">headline metrics <Info text="Post-warm-up means per run: the same numbers as the table, laid out for reading across runs. The live run's are running means of its samples so far." /></span></div>
          <table className="mini">
            <thead>
              <tr><th>run</th>{COLS.map(c => { const k = KPIS.find(x => x.key === c.kpi); return <th key={c.key} className="num">{k.label}</th> })}</tr>
            </thead>
            <tbody>
              {shown.map(r => {
                const sum = summaryOf(r)
                return (
                  <tr key={r.id}>
                    <td><span className="swatch" style={{ background: colorOf(r) }} />#{r.id} {r.dispatcher}{r.id === liveRun?.id ? ' (live)' : r.id === baseline ? ' (baseline)' : ''}</td>
                    {COLS.map(c => { const k = KPIS.find(x => x.key === c.kpi); const v = sum[c.key]; return <td key={c.key} className="num">{v == null ? '—' : k.fmt(Number(v))}</td> })}
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
        <div className="kpi-card">
          <div className="kpi-head"><span className="stat-label">where cycle time goes <Info text={SPLIT_INFO} /></span><span className="muted" style={{ fontSize: 11 }}>lot-hours over each run's streamed span</span></div>
          {shown.map(r => (
            <SplitBar key={r.id} split={splitOfRun(series[r.id])}
                      label={`#${r.id} ${r.dispatcher}${r.id === liveRun?.id ? ' (live)' : r.id === baseline ? ' (baseline)' : ''}`} />
          ))}
          <div className="legend" style={{ marginTop: 6, fontSize: 11 }}>
            {SPLIT.map(s => <span key={s.key} style={{ marginRight: 12 }}><span className="swatch" style={{ background: s.color }} />{s.label}</span>)}
          </div>
        </div>
      </div>

      <h4 style={{ margin: '18px 0 6px' }}>
        Busiest tools and toolsets{' '}
        <select value={detailId ?? (liveRun?.id ?? baseline ?? '')} onChange={e => setDetailId(Number(e.target.value) || null)}
                style={{ font: 'inherit', fontSize: 12, marginLeft: 8 }}>
          {shown.map(r => <option key={r.id} value={r.id}>#{r.id} {r.dispatcher}/{r.batch_strat} s{r.seed} · {r.status}</option>)}
        </select>
      </h4>
      {(() => {
        const id = detailId ?? (liveRun?.id ?? baseline)
        const r = runs.find(x => x.id === id)
        return r ? <RunDetail run={r} color={colorOf(r)} /> : null
      })()}

      <h4 style={{ margin: '18px 0 6px' }}>KPIs over simulated time, run by run</h4>
      <p className="muted" style={{ marginTop: 0, fontSize: 11 }}>
        Faint = warm-up (shared history before the run's own dispatching began).
        {liveRun ? ' The live run\'s line grows as the fab streams.' : ' No run is streaming right now.'}
      </p>
      <div className="kpi-grid">
        {KPIS.map(k => (
          <div key={k.key} className="kpi-card">
            <div className="kpi-head">
              <span className="stat-label">{k.label} <Info text={k.info} /></span>
              <span className="muted" style={{ fontSize: 11 }}>{k.unit}</span>
            </div>
            <MultiChart kpi={k} series={chartSeries} />
          </div>
        ))}
      </div>
      <div className="legend" style={{ marginTop: 8 }}>
        {chartSeries.map(s => {
          const r = runs.find(x => x.id === s.id)
          return (
            <span key={s.id} style={{ marginRight: 14, fontSize: 11 }}>
              <span className="swatch" style={{ background: s.color }} />
              #{r.id} {r.dispatcher}/{r.batch_strat} s{r.seed}{s.live ? ' (live)' : ''}{s.baseline ? ' (baseline)' : ''}
            </span>
          )
        })}
      </div>
    </div>
  )
}
