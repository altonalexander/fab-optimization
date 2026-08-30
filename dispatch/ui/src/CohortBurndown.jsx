import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  batchBands as computeBands, domain, envelope as computeEnvelope,
  maxValue, segments as computeSegments,
} from './burndown_geom.js'

/**
 * Cohort burndown — remaining route steps per lot against simulated time.
 *
 * Drawn as raw SVG rather than with Recharts. Three things here do not fit a
 * chart library's data model: horizontal runs coloured by *why* the lot was
 * waiting, a dashed required-rate line per lot, and a min/max envelope over an
 * irregular per-lot time series. Fighting a library into that shape costs more
 * than the ~200 lines of geometry below.
 *
 * What "cohort" means is decided upstream in bench/tools/sim_feed.py: by
 * default a cohort is one product's releases within one day, because a furnace
 * batch in SMT2020 requires the same product *and* the same step, so only
 * same-product lots can ever be batch partners.
 */

// Eight hues, cycled. Per the brief: never generate a colour per lot -- past
// about eight, adjacent hues stop being distinguishable and the legend becomes
// the only way to read the chart, which defeats it.
const HUES = ['#2563eb', '#dc2626', '#059669', '#d97706',
              '#7c3aed', '#0891b2', '#be185d', '#4d7c0f']

// Why a lot sat still. `cohort` is the one the simulator can name from data:
// it tracks waiting_time_batching separately, so a flat run dominated by it is
// genuinely "waiting for batch partners" rather than an inference.
const REASON = {
  cohort: { label: 'waiting on cohort', color: '#7c3aed' },
  queue:  { label: 'queued for a tool', color: '#9ca3af' },
  proc:   { label: 'processing',        color: '#059669' },
  none:   { label: 'no movement yet',   color: '#d1d5db' },
}

const DAY = 86400
const fmtDay = t => `d${(t / DAY).toFixed(1)}`
const hueFor = (key, i) => HUES[i % HUES.length]

export default function CohortBurndown() {
  const [index, setIndex] = useState(null)
  const [cohort, setCohort] = useState(null)
  const [data, setData] = useState(null)
  const [mode, setMode] = useState('envelope')   // envelope | lines
  const [metric, setMetric] = useState('steps')  // steps | time
  const [focus, setFocus] = useState(null)       // lot id
  const [err, setErr] = useState(null)
  const [zoom, setZoom] = useState({ scale: 1, offset: 0 })

  // --- live append ----------------------------------------------------------
  // Buffer SSE events and flush on an interval. Fab event rates are bursty:
  // LVHM emits ~23k progress events per simulated day, and re-rendering per
  // event pins the tab. The connection is opened here rather than shared with
  // App so this high-rate stream cannot re-render the rest of the dashboard.
  const pending = useRef([])
  const cohortRef = useRef(null)
  cohortRef.current = cohort

  useEffect(() => {
    let es
    try {
      es = new EventSource('/api/stream')
      es.onmessage = e => {
        let msg
        try { msg = JSON.parse(e.data) } catch { return }
        if (msg.kind !== 'event') return
        const ev = msg.event
        if (!ev || ev.type !== 'LOT_PROGRESS') return
        if (!cohortRef.current || ev.cohort !== cohortRef.current) return
        pending.current.push(ev)
      }
    } catch { /* stream is optional; the fetch below still populates */ }
    return () => es && es.close()
  }, [])

  useEffect(() => {
    const id = setInterval(() => {
      if (!pending.current.length) return
      const batch = pending.current
      pending.current = []
      setData(prev => {
        if (!prev) return prev
        const lots = prev.lots.map(l => ({ ...l, points: l.points }))
        const byId = new Map(lots.map(l => [l.lot, l]))
        let now = prev.now_t
        for (const ev of batch) {
          const t = Number(ev.t)
          if (t > now) now = t
          const pt = {
            t, left: Number(ev.left), reason: ev.reason || 'none',
            wq: Number(ev.wq || 0), wb: Number(ev.wb || 0),
            wp: Number(ev.wp || 0), rem_s: Number(ev.rem_s || 0),
          }
          const l = byId.get(ev.lot)
          if (l) {
            l.points = [...l.points, pt]
            l.state = ev.state || l.state
            l.route = Math.max(l.route || 0, Number(ev.route) || 0)
          } else {
            const nl = {
              lot: ev.lot, part: ev.part, route: Number(ev.route) || 0,
              release: Number(ev.rel) || 0, due: Number(ev.due) || 0,
              prio: Number(ev.prio) || 0, state: ev.state || 'active',
              points: [pt],
            }
            lots.push(nl); byId.set(ev.lot, nl)
          }
        }
        return { ...prev, lots, now_t: now }
      })
    }, 1000)   // ~1 Hz, per the brief
    return () => clearInterval(id)
  }, [])

  // --- initial load ---------------------------------------------------------
  const loadIndex = useCallback(() => {
    fetch('/api/lots?limit=60')
      .then(r => r.json())
      .then(d => {
        setIndex(d)
        setErr(null)
        if (!cohortRef.current && d.cohorts && d.cohorts.length) {
          setCohort(d.cohorts[0].cohort)
        }
      })
      .catch(() => setErr('could not reach /api/lots'))
  }, [])

  useEffect(() => { loadIndex() }, [loadIndex])

  useEffect(() => {
    if (!cohort) return
    setData(null)
    pending.current = []
    setZoom({ scale: 1, offset: 0 })
    fetch(`/api/lots/${encodeURIComponent(cohort)}`)
      .then(r => r.json())
      .then(setData)
      .catch(() => setErr(`could not load cohort ${cohort}`))
  }, [cohort])

  // --- geometry -------------------------------------------------------------
  const W = 900, H = 420, M = { t: 16, r: 18, b: 40, l: 56 }
  const iw = W - M.l - M.r, ih = H - M.t - M.b

  const view = useMemo(() => {
    if (!data || !data.lots || !data.lots.length) return null
    const lots = data.lots
    const now = data.now_t ?? 0

    // Fixed window. An auto-scaling axis would shift the chart under the
    // reader's cursor on every tick, so the domain only moves when they zoom
    // or pan.
    const [t0, t1] = domain(lots, now)
    const span = (t1 - t0) / zoom.scale
    const d0 = t0 + zoom.offset * (t1 - t0)
    const d1 = d0 + span

    const yMax = maxValue(lots, metric)
    const x = t => M.l + ((t - d0) / (d1 - d0)) * iw
    const y = v => M.t + ih - (v / yMax) * ih
    return { lots, now, d0, d1, yMax, x, y }
  }, [data, metric, zoom, iw, ih, M.l, M.t])

  // Y positions at which lots in this cohort were actually observed waiting on
  // batch partners. The brief asks for the route's batch steps; SMT2020 does
  // not put those on the wire, so these are the *observed* ones -- honest, and
  // arguably better, but it means a batch step nobody has reached yet is not
  // marked. Labelled as observed in the legend for that reason.
  const batchBands = useMemo(
    () => (view && metric === 'steps' ? computeBands(view.lots) : []),
    [view, metric])

  const envelope = useMemo(() => {
    if (!view || mode !== 'envelope') return null
    const e = computeEnvelope(view.lots, view.d0, view.d1, view.now, metric)
    return e.length ? e : null
  }, [view, mode, metric])

  if (err) return <p className="muted">{err}</p>
  if (!index) return <p className="muted">loading cohorts…</p>
  if (!index.cohorts || !index.cohorts.length) {
    return (
      <div>
        <p className="muted">
          No burndown points yet. The lots view is fed by <code>LOT_PROGRESS</code>{' '}
          events from <code>bench/tools/sim_feed.py</code>; start the feed, or check
          it was not run with <code>--no-burndown</code>.
        </p>
      </div>
    )
  }

  const row = index.cohorts.find(c => c.cohort === cohort)

  return (
    <div className="burndown">
      <div className="burndown-controls">
        <select value={cohort || ''} onChange={e => setCohort(e.target.value)}>
          {index.cohorts.map(c => (
            <option key={c.cohort} value={c.cohort}>
              {c.cohort} — {c.lots} lots, spread {c.spread}
              {c.done ? `, ${c.done} done` : ''}
            </option>
          ))}
        </select>

        <span className="seg">
          <button className={mode === 'envelope' ? 'active' : ''}
                  onClick={() => setMode('envelope')}>envelope</button>
          <button className={mode === 'lines' ? 'active' : ''}
                  onClick={() => setMode('lines')}>lots</button>
        </span>

        <span className="seg">
          <button className={metric === 'steps' ? 'active' : ''}
                  onClick={() => setMetric('steps')}>steps left</button>
          <button className={metric === 'time' ? 'active' : ''}
                  onClick={() => setMetric('time')}
                  title="Sum of raw process time over the remaining route. Queue time is NOT included, so this is a floor, not a predicted finish.">
            process time left
          </button>
        </span>

        <span className="seg">
          <button onClick={() => setZoom(z => ({ ...z, scale: z.scale * 1.6 }))}>+</button>
          <button onClick={() => setZoom(z => ({ ...z, scale: Math.max(1, z.scale / 1.6) }))}>−</button>
          <button onClick={() => setZoom({ scale: 1, offset: 0 })}>reset</button>
        </span>
      </div>

      {row && (
        <p className="muted" style={{ marginTop: 2 }}>
          <b>{row.lots}</b> lots of <b>{row.part}</b>, spread{' '}
          <b>{row.spread}</b> steps between fastest and slowest.{' '}
          {row.spread > 0
            ? 'A widening band means the cohort is desynchronising and will stall at the next batch step.'
            : 'The cohort is still together.'}
        </p>
      )}

      <svg width={W} height={H} className="burndown-svg" role="img"
           aria-label="cohort burndown">
        {/* batch-step bands: flat spots at these Y values are expected */}
        {batchBands.map(v => (
          <line key={`b${v}`} x1={M.l} x2={W - M.r} y1={view.y(v)} y2={view.y(v)}
                stroke="#7c3aed" strokeOpacity="0.28" strokeDasharray="2 3" />
        ))}

        {/* axes */}
        <line x1={M.l} x2={W - M.r} y1={M.t + ih} y2={M.t + ih} stroke="#9ca3af" />
        <line x1={M.l} x2={M.l} y1={M.t} y2={M.t + ih} stroke="#9ca3af" />
        {view && [0, 0.25, 0.5, 0.75, 1].map(f => {
          const v = view.yMax * f
          return (
            <g key={f}>
              <line x1={M.l} x2={W - M.r} y1={view.y(v)} y2={view.y(v)}
                    stroke="#e5e7eb" />
              <text x={M.l - 6} y={view.y(v) + 4} textAnchor="end" fontSize="10"
                    fill="#6b7280">
                {metric === 'steps' ? Math.round(v) : `${(v / 3600).toFixed(0)}h`}
              </text>
            </g>
          )
        })}
        {view && [0, 0.25, 0.5, 0.75, 1].map(f => {
          const t = view.d0 + f * (view.d1 - view.d0)
          return (
            <text key={`x${f}`} x={view.x(t)} y={H - 22} textAnchor="middle"
                  fontSize="10" fill="#6b7280">{fmtDay(t)}</text>
          )
        })}

        {view && envelope && (
          <>
            <path d={areaPath(envelope, view)} fill="#2563eb" fillOpacity="0.16" />
            <path d={linePath(envelope, view, 'med')} fill="none"
                  stroke="#2563eb" strokeWidth="2" />
          </>
        )}

        {view && mode === 'lines' && view.lots.map((l, i) => {
          const dim = focus && focus !== l.lot
          const hue = hueFor(l.lot, i)
          return (
            <g key={l.lot} onClick={() => setFocus(focus === l.lot ? null : l.lot)}
               style={{ cursor: 'pointer' }}>
              {/* required rate: release -> due. "Line above its dash" == late. */}
              <line x1={view.x(l.release)} y1={view.y(metric === 'steps'
                        ? (l.route || 0)
                        : (l.points[0]?.rem_s ?? 0))}
                    x2={view.x(l.due)} y2={view.y(0)}
                    stroke={hue} strokeOpacity={dim ? 0.08 : 0.4}
                    strokeDasharray="4 4" />
              {segments(l, view, metric).map((s, k) => (
                <line key={k} x1={s.x1} y1={s.y1} x2={s.x2} y2={s.y2}
                      stroke={dim ? '#d1d5db'
                        : (s.flat ? (REASON[s.reason] || REASON.none).color : hue)}
                      strokeWidth={s.flat ? 3 : 1.5}
                      strokeOpacity={dim ? 0.5 : 1} />
              ))}
            </g>
          )
        })}

        {/* now */}
        {view && view.now >= view.d0 && view.now <= view.d1 && (
          <>
            <line x1={view.x(view.now)} x2={view.x(view.now)} y1={M.t} y2={M.t + ih}
                  stroke="#111827" strokeDasharray="3 3" />
            <text x={view.x(view.now) + 4} y={M.t + 10} fontSize="10" fill="#111827">
              now
            </text>
          </>
        )}
      </svg>

      <div className="burndown-legend">
        {Object.entries(REASON).map(([k, v]) => (
          <span key={k}><i style={{ background: v.color }} />{v.label}</span>
        ))}
        <span><i style={{ background: '#7c3aed', opacity: 0.3 }} />batch step (observed)</span>
        <span className="muted">dashed = required rate to due date</span>
      </div>

      <p className="muted burndown-note">
        Steps are not equal work: forty metrology steps and forty implant steps
        are very different amounts of cycle time, so step count flatters lots
        with a long tail of quick operations. <b>process time left</b> sums the
        raw process time over the remaining route — it excludes queue time, which
        the simulator does not forecast, so it is a floor rather than a predicted
        finish. The line is <b>not monotonic</b>: rework splices completed steps
        back onto the route and the burndown goes up. That jog is real.
      </p>
    </div>
  )
}

/* ---------------------------------------------------------------------- */

/** Data-space segments mapped through the pixel scales. The geometry itself
 *  lives in burndown_geom.js so it can be tested without a DOM. */
function segments(lot, view, metric) {
  return computeSegments(lot, metric, view.now).map(s => ({
    x1: view.x(s.t1), y1: view.y(s.v1),
    x2: view.x(s.t2), y2: view.y(s.v2),
    flat: s.flat, reason: s.reason,
  }))
}

function linePath(env, view, key) {
  return env.map((e, i) =>
    `${i ? 'L' : 'M'}${view.x(e.t).toFixed(1)},${view.y(e[key]).toFixed(1)}`
  ).join('')
}

function areaPath(env, view) {
  const top = env.map((e, i) =>
    `${i ? 'L' : 'M'}${view.x(e.t).toFixed(1)},${view.y(e.max).toFixed(1)}`).join('')
  const bot = env.slice().reverse().map(e =>
    `L${view.x(e.t).toFixed(1)},${view.y(e.min).toFixed(1)}`).join('')
  return `${top}${bot}Z`
}
