import { useEffect, useMemo, useState } from 'react'

/**
 * Fab KPIs since day 0, as small multiples.
 *
 * Every series comes from /api/kpi, which the producer samples once per
 * simulated hour over a trailing simulated day -- during warm-up and during
 * the live run alike, by the same code. That is what makes the black half
 * (before the sim you are watching started) comparable to the blue half, and
 * what will make a later run under a different dispatcher comparable to this
 * one: same definitions, same cadence, different run id.
 */

// One entry per KPI. `key` is the field on a sample; `fmt` renders the latest
// value for the tile; `unit` is the axis unit; `pct` pins the y axis to 0-100.
export const KPIS = [
  { key: 'wip', label: 'WIP', unit: 'lots',
    fmt: v => Math.round(v).toLocaleString(),
    info: 'Lots in the fab: released and not yet complete. Waiting in a queue plus on a tool. The producer counts the simulator\'s active lots directly, so this is the ground truth the header\'s ready + in flight should add up to.' },
  { key: 'thr', label: 'throughput', unit: 'lots/day',
    fmt: v => Math.round(v).toLocaleString(),
    info: 'Lots that completed their whole route in the trailing simulated day. Independent of playback speed. LVHM releases about 56 lots/day, so a steady fab completes about that many.' },
  { key: 'starts', label: 'starts', unit: 'lots/day',
    fmt: v => Math.round(v).toLocaleString(),
    info: 'Lots released into the fab in the trailing simulated day. Today this is the dataset\'s order schedule verbatim (order.txt: each product releases on a fixed interval) — the dispatcher does not touch it. Starts above throughput means WIP is building. A release policy (CONWIP, workload regulation, mix changes) would be the second lever beside dispatch, and this is the number it would be judged on.' },
  { key: 'ct', label: 'cycle time', unit: 'days',
    fmt: v => v.toFixed(1),
    info: 'Mean release-to-complete time, in simulated days, of the lots completed in the trailing day. Lots that were already in the fab at day 0 carry their WIP.txt release date, so early values are dominated by that inherited history.' },
  { key: 'otd', label: 'on-time delivery', unit: '%', pct: true,
    fmt: v => v.toFixed(0) + '%',
    info: 'Share of lots completed in the trailing day that finished on or before their due date. Pairs with mean tardiness (hover the tile) for how late the late ones were.' },
  { key: 'util', label: 'tool utilization', unit: '%', pct: true,
    fmt: v => v.toFixed(0) + '%',
    info: 'Share of real tools with at least one lot processing on them at the sample instant. The Delay_* pseudo-toolset (400 stations standing in for route-prescribed waits) is excluded from both sides. Includes tools that are down (they count as not busy), so it is utilization of the installed base, not of available capacity.' },
  { key: 'optpct', label: 'optimized decisions', unit: '%', pct: true,
    fmt: v => v.toFixed(0) + '%',
    derive: s => (s.dec ? 100 * s.opt / s.dec : 0),
    info: 'Dispatch decisions in the trailing day (real tools only; Delay_* pseudo-tools are not decisions) that came from the optimizing dispatcher rather than falling back to the default rule. The baseline runs the default rule (fifo) for everything, so this reads 0% until the dispatcher runs inside the simulator; then it is the first number to watch.' },
]

// Where a lot's time goes, as shares of the lot-hours in the trailing day.
export const SPLIT = [
  { key: 'wq', label: 'queueing for a tool', color: '#6b7280' },
  { key: 'wb', label: 'holding for batch partners', color: '#7c3aed' },
  { key: 'wp', label: 'processing', color: '#059669' },
  { key: 'wd', label: 'delay steps', color: '#a3a3a3' },
]
export const SPLIT_INFO = 'Lot-hours in the trailing simulated day split by what the lot was doing: waiting in a queue for a tool, held back waiting for batch partners at a furnace, processing on a real tool, or sitting in a route-prescribed delay step (cure, cool-down — the Delay_* pseudo-toolset, which never queues). Queue share is the part dispatching can move; processing and delay are the floor set by the routes.'

export function splitOf(sample) {
  if (!sample) return null
  const q = Number(sample.wq || 0), b = Number(sample.wb || 0), p = Number(sample.wp || 0), d = Number(sample.wd || 0)
  const tot = q + b + p + d
  return tot > 0 ? { wq: 100 * q / tot, wb: 100 * b / tot, wp: 100 * p / tot, wd: 100 * d / tot, tot } : null
}

export function SplitBar({ split, label, height = 22 }) {
  if (!split) return <div className="muted" style={{ fontSize: 11 }}>{label}: no samples yet</div>
  return (
    <div className="split-row">
      <div className="split-label">{label}</div>
      <div className="split-bar" style={{ height }}>
        {SPLIT.map(s => (
          <div key={s.key} title={`${s.label}: ${split[s.key].toFixed(1)}%`}
               style={{ width: `${split[s.key]}%`, background: s.color }}>
            {split[s.key] >= 8 ? `${split[s.key].toFixed(0)}%` : ''}
          </div>
        ))}
      </div>
    </div>
  )
}

export function valueOf(kpi, sample) {
  if (!sample) return null
  const v = kpi.derive ? kpi.derive(sample) : sample[kpi.key]
  return v == null ? null : Number(v)
}

const HISTORIC = '#111827'
const LIVE = '#2563eb'

function Small({ kpi, hist, live, warm, now, w = 1200, h = 150 }) {
  const M = { l: 44, r: 12, t: 8, b: 20 }
  const iw = w - M.l - M.r, ih = h - M.t - M.b
  const all = hist.concat(live)
  const t0 = 0
  const t1 = Math.max(now || 0, all.length ? all[all.length - 1].t : 0, 86400) * 1.02
  let yMax = kpi.pct ? 100 : 0
  if (!kpi.pct) {
    for (const s of all) { const v = valueOf(kpi, s); if (v != null && v > yMax) yMax = v }
    yMax = yMax > 0 ? yMax * 1.1 : 1
  }
  const x = t => M.l + ((t - t0) / (t1 - t0)) * iw
  const y = v => M.t + ih - (Math.min(v, yMax) / yMax) * ih
  const path = pts => pts.map((s, i) => {
    const v = valueOf(kpi, s)
    return `${i ? 'L' : 'M'}${x(s.t).toFixed(1)},${y(v == null ? 0 : v).toFixed(1)}`
  }).join(' ')
  // Join the eras: the first live point continues from the last warm-up one.
  const liveSeg = hist.length && live.length ? [hist[hist.length - 1]].concat(live) : live
  const days = t => (t / 86400)
  const ticks = []
  const span = days(t1)
  const step = span > 120 ? 30 : span > 40 ? 10 : span > 12 ? 5 : 1
  for (let d = 0; d <= span; d += step) ticks.push(d)
  return (
    <svg width="100%" viewBox={`0 0 ${w} ${h}`} preserveAspectRatio="none"
         style={{ display: 'block', height: h }}>
      {[0, 0.5, 1].map(f => (
        <g key={f}>
          <line x1={M.l} x2={w - M.r} y1={y(f * yMax)} y2={y(f * yMax)}
                stroke="#e5e7eb" strokeWidth="1" />
          <text x={M.l - 4} y={y(f * yMax) + 3} fontSize="9" textAnchor="end" fill="#6b7280">
            {kpi.pct ? Math.round(f * 100) : (f * yMax >= 100 ? Math.round(f * yMax) : (f * yMax).toFixed(1))}
          </text>
        </g>
      ))}
      {ticks.map(d => (
        <text key={d} x={x(d * 86400)} y={h - 6} fontSize="9" textAnchor="middle" fill="#6b7280">
          d{d}
        </text>
      ))}
      {hist.length > 1 && (
        <path d={path(hist)} fill="none" stroke={HISTORIC} strokeWidth="1.4" strokeOpacity="0.8" />
      )}
      {liveSeg.length > 1 && (
        <path d={path(liveSeg)} fill="none" stroke={LIVE} strokeWidth="1.6" />
      )}
      {warm != null && (
        <line x1={x(warm)} x2={x(warm)} y1={M.t} y2={M.t + ih}
              stroke="#111827" strokeWidth="1" strokeDasharray="3 3" strokeOpacity="0.6" />
      )}
    </svg>
  )
}

/**
 * Whole-fab WIP since day 0 as waiting / running, from the producer's hourly
 * samples (wip and running are both on every sample; waiting is the
 * difference). Black before sim start, coloured after, like the KPI charts.
 */
export function WipSinceDay0({ w = 700, h = 220 }) {
  const [data, setData] = useState(null)
  useEffect(() => {
    let alive = true
    const load = () => fetch('/api/kpi').then(r => r.json())
      .then(d => alive && setData(d)).catch(() => {})
    load()
    const id = setInterval(load, 10000)
    return () => { alive = false; clearInterval(id) }
  }, [])
  if (!data) return <p className="muted">loading…</p>
  const hist = data.hist || [], live = data.live || []
  const all = hist.concat(live)
  if (all.length < 2) return <p className="muted">no samples yet</p>
  const M = { l: 48, r: 12, t: 8, b: 20 }
  const iw = w - M.l - M.r, ih = h - M.t - M.b
  const t1 = Math.max(data.now_t || 0, all[all.length - 1].t, 86400) * 1.02
  let yMax = 0
  for (const s of all) yMax = Math.max(yMax, Number(s.wip || 0))
  yMax = yMax > 0 ? yMax * 1.1 : 1
  const x = t => M.l + (t / t1) * iw
  const y = v => M.t + ih - (Math.min(v, yMax) / yMax) * ih
  const SER = [
    { key: 'waiting', name: 'waiting', color: '#1d4ed8', of: s => Number(s.wip || 0) - Number(s.running || 0) },
    { key: 'running', name: 'running', color: '#059669', of: s => Number(s.running || 0) },
    { key: 'wip', name: 'total WIP', color: '#6b7280', of: s => Number(s.wip || 0) },
  ]
  const path = (pts, of) => pts.map((s, i) => `${i ? 'L' : 'M'}${x(s.t).toFixed(1)},${y(of(s)).toFixed(1)}`).join(' ')
  const liveSeg = hist.length && live.length ? [hist[hist.length - 1]].concat(live) : live
  const span = t1 / 86400
  const step = span > 120 ? 30 : span > 40 ? 10 : span > 12 ? 5 : 1
  const ticks = []
  for (let d = 0; d <= span; d += step) ticks.push(d)
  const last = all[all.length - 1]
  return (
    <div>
      <div className="legend" style={{ fontSize: 12, marginBottom: 4 }}>
        {SER.map(s => (
          <span key={s.key} style={{ marginRight: 14 }}>
            <span className="swatch" style={{ background: s.color }} />{s.name}{' '}
            <b>{Math.round(s.of(last)).toLocaleString()}</b>
          </span>
        ))}
        <span className="muted">· black = warm-up, before sim start</span>
      </div>
      <svg width="100%" viewBox={`0 0 ${w} ${h}`} preserveAspectRatio="none" style={{ display: 'block', height: h }}>
        {[0, 0.25, 0.5, 0.75, 1].map(f => (
          <g key={f}>
            <line x1={M.l} x2={w - M.r} y1={y(f * yMax)} y2={y(f * yMax)} stroke="#e5e7eb" />
            <text x={M.l - 4} y={y(f * yMax) + 3} fontSize="9" textAnchor="end" fill="#6b7280">{Math.round(f * yMax)}</text>
          </g>
        ))}
        {ticks.map(d => <text key={d} x={x(d * 86400)} y={h - 6} fontSize="9" textAnchor="middle" fill="#6b7280">d{d}</text>)}
        {SER.map(s => (
          <g key={s.key}>
            {hist.length > 1 && <path d={path(hist, s.of)} fill="none" stroke="#111827" strokeWidth={s.key === 'wip' ? 1.6 : 1.2} strokeOpacity={s.key === 'wip' ? 0.8 : 0.45} />}
            {liveSeg.length > 1 && <path d={path(liveSeg, s.of)} fill="none" stroke={s.color} strokeWidth="1.8" />}
          </g>
        ))}
        {data.warm_t != null && (
          <line x1={x(data.warm_t)} x2={x(data.warm_t)} y1={M.t} y2={M.t + ih} stroke="#111827" strokeDasharray="3 3" strokeOpacity="0.6" />
        )}
      </svg>
    </div>
  )
}

export function Info({ text }) {
  // A visible marker plus the native tooltip: reliable on every browser, needs
  // no positioning code, and reads on hover or long-press.
  return <span className="info" title={text} aria-label={text} role="img">i</span>
}

export default function KpiPanel() {
  const [data, setData] = useState(null)
  const [err, setErr] = useState(null)
  useEffect(() => {
    let alive = true
    const load = () => fetch('/api/kpi').then(r => r.json())
      .then(d => { if (alive) { setData(d); setErr(null) } })
      .catch(() => alive && setErr('could not load /api/kpi'))
    load()
    const id = setInterval(load, 10000)
    return () => { alive = false; clearInterval(id) }
  }, [])

  const latest = data?.latest
  const view = useMemo(() => data ? {
    hist: data.hist || [], live: data.live || [],
    warm: data.warm_t, now: data.now_t,
  } : null, [data])

  if (err) return <p className="muted">{err}</p>
  if (!view) return <p className="muted">loading KPIs…</p>
  if (!view.hist.length && !view.live.length) {
    return (
      <p className="muted">
        No KPI samples yet. The producer publishes one per simulated hour on{' '}
        <code>fab.kpi.state</code>; the warm-up series arrives with the snapshot.
      </p>
    )
  }
  return (
    <div>
      <div className="kpi-grid">
        {KPIS.map(k => {
          const v = valueOf(k, latest)
          return (
            <div key={k.key} className="kpi-card">
              <div className="kpi-head">
                <span className="stat-label">{k.label} <Info text={k.info} /></span>
                <span className="kpi-now">{v == null ? '—' : k.fmt(v)} <small>{k.unit}</small></span>
              </div>
              <Small kpi={k} hist={view.hist} live={view.live} warm={view.warm} now={view.now} />
            </div>
          )
        })}
      </div>
      <div className="kpi-card" style={{ marginTop: 12 }}>
        <div className="kpi-head">
          <span className="stat-label">where cycle time goes <Info text={SPLIT_INFO} /></span>
          <span className="muted" style={{ fontSize: 11 }}>trailing simulated day</span>
        </div>
        <SplitBar split={splitOf(latest)} label="now" />
        <div className="legend" style={{ marginTop: 6, fontSize: 11 }}>
          {SPLIT.map(s => <span key={s.key} style={{ marginRight: 12 }}><span className="swatch" style={{ background: s.color }} />{s.label}</span>)}
        </div>
      </div>
      <p className="muted" style={{ fontSize: 11, marginTop: 8 }}>
        <span style={{ color: HISTORIC }}>■</span> warm-up (before sim start) ·{' '}
        <span style={{ color: LIVE }}>■</span> this run · dashed rule = sim start ·
        one sample per simulated hour; throughput, cycle time and on-time over the
        trailing simulated day. Run {data.run || '—'}.
      </p>
    </div>
  )
}
