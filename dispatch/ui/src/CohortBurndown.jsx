import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import LotJourneys from './LotJourneys.jsx'
import {
  batchBands as computeBands, envelope as computeEnvelope,
  maxValue, segments as computeSegments, projection as computeProjection,
  reworkJogs, domainWithProjection, historySegments, allPoints,
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

// Warm-up is drawn in near-black. It is not a category of wait like the
// reason colours below -- it is a different era, before the run being watched
// began, and colouring it with the live palette invites reading a warm-up
// stall as something this run caused.
const HISTORIC = '#111827'

const DAY = 86400
const fmtDay = t => `d${(t / DAY).toFixed(1)}`
const hueFor = (key, i) => HUES[i % HUES.length]
const fmtDur = sec => {
  if (sec == null) return '—'
  const d = sec / 86400
  return Math.abs(d) >= 1 ? `${d.toFixed(1)}d` : `${(sec / 3600).toFixed(1)}h`
}
const fmtSigned = sec =>
  sec == null ? '—' : `${sec >= 0 ? '+' : ''}${(sec / 86400).toFixed(1)}d`

/**
 * `cohort`/`onCohort` are optional. When App passes them the selection lives in
 * the URL (#/lots?cohort=part_3-d0), which is what lets a route page link to
 * one cohort; unpassed, the component keeps its own selection and behaves as
 * it did before. `routeHref` builds the link back to a product's route page.
 * `header` is the chart section's heading, rendered here so the lot journeys
 * (which share this component's fetch) can sit in their own section above it.
 */
export default function CohortBurndown({ cohort: cohortProp, onCohort,
                                         routeHref, header = null } = {}) {
  const [index, setIndex] = useState(null)
  const [ownCohort, setOwnCohort] = useState(null)
  const controlled = typeof onCohort === 'function'
  const cohort = controlled ? cohortProp : ownCohort
  const setCohort = controlled ? onCohort : setOwnCohort
  // Held in a ref, not a dependency: App passes an inline arrow, so a direct
  // dependency would give the index fetch a new identity every parent render
  // and re-fetch /api/lots on each one.
  const setCohortRef = useRef(setCohort)
  setCohortRef.current = setCohort
  const [data, setData] = useState(null)
  const [mode, setMode] = useState('envelope')   // envelope | lines
  // Steps left is the one metric drawn. "Process time left" was a toggle
  // here; it excluded queue time, which is most of the cycle, and read as a
  // forecast it was not.
  const metric = 'steps'
  // Context around the cohort: its nearest earlier and later cohorts of the
  // same product, and hot lots released before it that may catch it up.
  const [neigh, setNeigh] = useState(0)          // 0 | 2 | 5 cohorts each side
  const [hotN, setHotN] = useState(0)            // 0 | 3 | 6 hot lots
  const [partIndex, setPartIndex] = useState(null)
  const [extra, setExtra] = useState({})         // id -> payload (cohort or hot)
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
        // Only when nothing is selected: a URL naming a cohort must win over
        // "the most recently moved one", or a pasted link would bounce to a
        // different cohort the moment the index arrived.
        if (!cohortRef.current && d.cohorts && d.cohorts.length) {
          setCohortRef.current(d.cohorts[0].cohort)
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

  // --- context: neighbours and hot lots --------------------------------------
  const rowNow = index?.cohorts?.find(c => c.cohort === cohort) || null
  const part = rowNow?.part || data?.lots?.[0]?.part || null
  useEffect(() => {
    if (!part || (!neigh && !hotN)) { setPartIndex(null); return }
    fetch(`/api/lots?part=${encodeURIComponent(part)}`).then(r => r.json())
      .then(d => setPartIndex(d.cohorts || [])).catch(() => setPartIndex([]))
  }, [part, neigh, hotN, cohort])

  // Nearest by release AND due date together: two cohorts a day apart in
  // release but a week apart in due date (a hot stream vs a regular one)
  // are not neighbours in the sense that matters -- meeting at a furnace.
  const neighbours = useMemo(() => {
    if (!neigh || !partIndex || !rowNow) return []
    const near = c => Math.abs(c.release - rowNow.release) + Math.abs((c.due || 0) - (rowNow.due || 0))
    const others = partIndex.filter(c => c.cohort !== cohort)
    const before = others.filter(c => c.release < rowNow.release).sort((a, b) => near(a) - near(b)).slice(0, neigh)
    const after = others.filter(c => c.release >= rowNow.release).sort((a, b) => near(a) - near(b)).slice(0, neigh)
    return before.map(c => ({ ...c, side: 'before' })).concat(after.map(c => ({ ...c, side: 'after' })))
  }, [neigh, partIndex, rowNow, cohort])

  useEffect(() => {
    let alive = true
    const want = neighbours.map(c => `/api/lots/${encodeURIComponent(c.cohort)}`)
    if (hotN && part && rowNow) {
      want.push(`/api/lots/hot?part=${encodeURIComponent(part)}&near_t=${rowNow.release}`
                + `&min_left=${rowNow.min_left || 0}&limit=${hotN}`)
    }
    if (!want.length) { setExtra({}); return }
    Promise.all(want.map(u => fetch(u).then(r => r.json()).then(d => [u, d]).catch(() => [u, null])))
      .then(pairs => { if (alive) setExtra(Object.fromEntries(pairs.filter(p => p[1]))) })
    return () => { alive = false }
  }, [neighbours, hotN, part, rowNow?.release, rowNow?.min_left])

  const extraLots = useMemo(() => Object.values(extra).flatMap(d => d.lots || []), [extra])

  // --- geometry -------------------------------------------------------------
  // The chart is drawn at its container's real width rather than at a fixed
  // 900px scaled down by CSS: scaling the whole SVG shrinks the axis labels
  // and strokes with it, and on a wide screen it left the panel half empty.
  // Only the width is measured -- height stays fixed, so the aspect ratio
  // changes with the window instead of the chart growing unboundedly tall.
  const wrapRef = useRef(null)
  const [W, setW] = useState(900)
  useEffect(() => {
    const el = wrapRef.current
    if (!el || typeof ResizeObserver === 'undefined') return
    const ro = new ResizeObserver(([e]) => {
      const w = Math.round(e.contentRect.width)
      if (w > 0) setW(prev => (Math.abs(prev - w) >= 1 ? w : prev))
    })
    ro.observe(el)
    return () => ro.disconnect()
  }, [])

  const H = 420, M = { t: 16, r: 18, b: 40, l: 56 }
  const iw = Math.max(120, W - M.l - M.r), ih = H - M.t - M.b

  const view = useMemo(() => {
    if (!data || !data.lots || !data.lots.length) return null
    const lots = data.lots
    const now = data.now_t ?? 0

    // Fixed window. An auto-scaling axis would shift the chart under the
    // reader's cursor on every tick, so the domain only moves when they zoom
    // or pan.
    // Context lots widen the window (a later cohort is due later) but do not
    // otherwise take part in the geometry of the selected cohort.
    const [t0, t1] = domainWithProjection(lots.concat(extraLots), now, metric)
    const span = (t1 - t0) / zoom.scale
    const d0 = t0 + zoom.offset * (t1 - t0)
    const d1 = d0 + span

    const warm = data.warm_t ?? null
    const yMax = maxValue(lots.concat(extraLots), metric)
    const x = t => M.l + ((t - d0) / (d1 - d0)) * iw
    const y = v => M.t + ih - (v / yMax) * ih
    return { lots, now, warm, d0, d1, yMax, x, y }
  }, [data, metric, zoom, iw, ih, M.l, M.t, extraLots])

  // Y positions at which lots in this cohort were actually observed waiting on
  // batch partners. The brief asks for the route's batch steps; SMT2020 does
  // not put those on the wire, so these are the *observed* ones -- honest, and
  // arguably better, but it means a batch step nobody has reached yet is not
  // marked. Labelled as observed in the legend for that reason.
  const batchBands = useMemo(
    () => (view && metric === 'steps' ? computeBands(view.lots) : []),
    [view, metric])

  const context = useMemo(() => {
    if (!view) return []
    const out = []
    for (const [url, d] of Object.entries(extra)) {
      if (!d || !d.lots || !d.lots.length) continue
      if (url.includes('/api/lots/hot?')) {
        // Each hot lot on its own: they are individuals, not a batch set.
        for (const l of d.lots) {
          const e = computeEnvelope([l], view.d0, view.d1, view.now, metric, 200, [view.warm])
          out.push({ id: l.lot, label: `${l.lot} (hot, ${l.cohort})`, kind: 'hot', pts: e })
        }
        continue
      }
      const n = neighbours.find(c => d.cohort === c.cohort)
      const e = computeEnvelope(d.lots, view.d0, view.d1, view.now, metric, 200, [view.warm])
      out.push({ id: d.cohort, label: d.cohort, kind: n?.side || 'after', pts: e,
                 rank: n ? neighbours.filter(c => c.side === n.side).indexOf(n) : 0 })
    }
    return out
  }, [extra, view, metric, neighbours])

  const envelope = useMemo(() => {
    if (!view || mode !== 'envelope') return null
    const e = computeEnvelope(view.lots, view.d0, view.d1, view.now, metric,
                              160, [view.warm])
    return e.length ? e : null
  }, [view, mode, metric])

  if (err) return <section>{header}<p className="muted">{err}</p></section>
  if (!index) return <section>{header}<p className="muted">loading cohorts…</p></section>
  if (!index.cohorts || !index.cohorts.length) {
    return (
      <section>
        {header}
        <p className="muted">
          No burndown points yet. The lots view is fed by <code>LOT_PROGRESS</code>{' '}
          events from <code>bench/tools/sim_feed.py</code>; start the feed, or check
          it was not run with <code>--no-burndown</code>.
        </p>
      </section>
    )
  }

  const row = index.cohorts.find(c => c.cohort === cohort)

  return (
    <>
    <section>
      <h3>Lot journeys</h3>
      <p className="muted" style={{ marginTop: -4 }}>
        Where each lot of <b>{cohort}</b> stands on its route: the two steps it
        has left, the step it is at, and the two ahead. Box width is the step's
        nominal process time; a lot on a tool counts down the time left.
      </p>
      {view
        ? <LotJourneys lots={view.lots} routeHref={routeHref}
                       clock={data ? { t: data.now_t, t_at: data.now_t_at,
                                       speed: data.speed, paused: data.paused } : null} />
        : <p className="muted">loading {cohort}…</p>}
    </section>

    <section>
    {header}
    <div className="burndown" ref={wrapRef}>
      <div className="burndown-controls">
        {/* A <select> cannot hold a link per option, so the route link for
            the selected cohort lives in the note below this row. */}
        <select value={cohort || ''} onChange={e => setCohort(e.target.value)}>
          {/* The index holds the 60 most recently moved cohorts. A link from a
              route page can name one outside that window, and a <select> whose
              value is not among its options renders blank, so it is added. */}
          {cohort && !row && <option value={cohort}>{cohort}</option>}
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

        <span className="seg" title="Show the nearest earlier and later cohorts of this product, chosen by release date and due date together, so they are the ones that will actually meet this cohort at a batch step.">
          <span className="seg-label">± cohorts</span>
          {[0, 2, 5].map(n => (
            <button key={n} className={neigh === n ? 'active' : ''}
                    onClick={() => setNeigh(n)}>{n === 0 ? 'off' : n}</button>
          ))}
        </span>

        <span className="seg" title="Hot lots (priority 20) of this product that are behind this cohort in the route (at least as many steps left as its lead lot), released nearest to it. They move faster, so they are the ones likely to catch it up and contend for the same batches.">
          <span className="seg-label">hot lots catching up</span>
          {[0, 3, 6].map(n => (
            <button key={n} className={hotN === n ? 'active' : ''}
                    onClick={() => setHotN(n)}>{n === 0 ? 'off' : n}</button>
          ))}
        </span>

        <span className="seg">
          <button onClick={() => setZoom(z => ({ ...z, scale: z.scale * 1.6 }))}>+</button>
          <button onClick={() => setZoom(z => ({ ...z, scale: Math.max(1, z.scale / 1.6) }))}>−</button>
          <button onClick={() => setZoom({ scale: 1, offset: 0 })}>reset</button>
        </span>
      </div>

      {row && (
        <p className="muted" style={{ marginTop: 2 }}>
          <b>{row.lots}</b> lots of{' '}
          {routeHref
            ? <a href={routeHref(row.part)} title={`route walked by ${row.part}`}>
                <b>{row.part}</b>
              </a>
            : <b>{row.part}</b>}
          , spread{' '}
          <b>{row.spread}</b> steps between fastest and slowest.{' '}
          {row.spread > 0
            ? 'A widening band means the cohort is desynchronising and will stall at the next batch step.'
            : 'The cohort is still together.'}
        </p>
      )}

      <svg width="100%" height={H} viewBox={`0 0 ${W} ${H}`}
           preserveAspectRatio="xMidYMid meet"
           className="burndown-svg" role="img" aria-label="cohort burndown">
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
          // End ticks anchor inward: centred, the first and last labels hang
          // past the plot area and get clipped by the SVG viewport.
          const anchor = f === 0 ? 'start' : f === 1 ? 'end' : 'middle'
          return (
            <text key={`x${f}`} x={view.x(t)} y={H - 22} textAnchor={anchor}
                  fontSize="10" fill="#6b7280">{fmtDay(t)}</text>
          )
        })}

        {/* Due dates as dots on the zero line: a lot is finished when its
            burndown reaches y=0, so the dot marks where the line has to land
            to be on time. Drawn in both modes -- in envelope mode the
            per-lot due rules are not drawn, and without these the chart shows
            no deadline at all. Lots in one cohort do not share a due date:
            they release hours apart, and the weekly prio-20 stream lands in
            the same product-day bucket with a much tighter allowance. */}
        {view && view.lots.map((l, i) => {
          if (!l.due || l.due < view.d0 || l.due > view.d1) return null
          const dim = focus && focus !== l.lot
          return (
            <g key={`due${l.lot}`}>
              <circle cx={view.x(l.due)} cy={view.y(0)} r={dim ? 2.5 : 3.5}
                      fill="#dc2626" fillOpacity={dim ? 0.3 : 0.9}
                      stroke="#fff" strokeWidth="1" />
              <title>{`${l.lot} due d${(l.due / 86400).toFixed(1)}`}</title>
            </g>
          )
        })}

        {view && context.map(c => {
          const pts = c.pts.filter(e => e.t <= view.d1 && e.t >= view.d0)
          if (pts.length < 2) return null
          const color = c.kind === 'hot' ? '#dc2626' : c.kind === 'before' ? '#0891b2' : '#d97706'
          const last = pts[pts.length - 1]
          return (
            <g key={`ctx${c.id}`}>
              <path d={linePath(pts, view, 'med')} fill="none" stroke={color}
                    strokeWidth={c.kind === 'hot' ? 1.4 : 1.6}
                    strokeOpacity={c.kind === 'hot' ? 0.9 : 0.55}
                    strokeDasharray={c.kind === 'hot' ? '5 3' : undefined} />
              <text x={Math.min(view.x(last.t) + 4, W - M.r - 4)} y={view.y(last.med) - 3}
                    fontSize="10" fill={color} textAnchor={view.x(last.t) + 60 > W - M.r ? 'end' : 'start'}>
                {c.label}
              </text>
            </g>
          )
        })}

        {view && envelope && (() => {
          // Split the band at the warm-up line and draw each side in its own
          // ink, so the same shape reads as two eras rather than one history.
          const w = view.warm
          const past = w == null ? [] : envelope.filter(e => e.t <= w)
          const live = w == null ? envelope : envelope.filter(e => e.t >= w)
          return (
            <>
              {past.length > 1 && (
                <>
                  <path d={areaPath(past, view)} fill={HISTORIC} fillOpacity="0.13" />
                  <path d={linePath(past, view, 'med')} fill="none"
                        stroke={HISTORIC} strokeWidth="2" strokeOpacity="0.75" />
                </>
              )}
              {live.length > 1 && (
                <>
                  <path d={areaPath(live, view)} fill="#2563eb" fillOpacity="0.16" />
                  <path d={linePath(live, view, 'med')} fill="none"
                        stroke="#2563eb" strokeWidth="2" />
                </>
              )}
            </>
          )
        })()}

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
              {/* Warm-up: everything before the run being watched began. */}
              {historySegments(l, metric).map((s, k) => (
                <line key={`h${k}`}
                      x1={view.x(s.t1)} y1={view.y(s.v1)}
                      x2={view.x(s.t2)} y2={view.y(s.v2)}
                      stroke={dim ? '#d1d5db' : HISTORIC}
                      strokeWidth={s.flat ? 2.5 : 1.2}
                      strokeOpacity={dim ? 0.4 : 0.75} />
              ))}

              {segments(l, view, metric).map((s, k) => (
                <line key={k} x1={s.x1} y1={s.y1} x2={s.x2} y2={s.y2}
                      stroke={dim ? '#d1d5db'
                        : (s.flat ? (REASON[s.reason] || REASON.none).color : hue)}
                      strokeWidth={s.flat ? 3 : 1.5}
                      strokeOpacity={dim ? 0.5 : 1} />
              ))}

              {/* Projected burndown: gray, so it never reads as measurement. */}
              {(() => {
                const pr = computeProjection(l, metric, view.now)
                if (!pr) return null
                return (
                  <line x1={view.x(pr.t1)} y1={view.y(pr.v1)}
                        x2={view.x(pr.t2)} y2={view.y(pr.v2)}
                        stroke="#6b7280" strokeWidth={dim ? 1 : 1.5}
                        strokeDasharray="5 4"
                        strokeOpacity={dim ? 0.25 : 0.85} />
                )
              })()}

              {/* Due date: a vertical rule, so "projection crosses zero left of
                  the rule" is on time and right of it is late, read directly. */}
              {l.due > view.d0 && l.due < view.d1 && (
                <line x1={view.x(l.due)} x2={view.x(l.due)}
                      y1={M.t} y2={M.t + ih}
                      stroke={hue} strokeDasharray="2 5"
                      strokeOpacity={dim ? 0.15 : 0.55} />
              )}

              {/* Rework: the burndown steps back up. Total route is unchanged;
                  the lot went back in the line and must redo those steps. */}
              {reworkJogs(l).map((j, k) => (
                <g key={`rw${k}`}>
                  <circle cx={view.x(j.t)} cy={view.y(j.to)} r={dim ? 2 : 3.2}
                          fill="none" stroke="#b45309"
                          strokeWidth="1.5" strokeOpacity={dim ? 0.3 : 1} />
                  <title>{`rework at d${(j.t / 86400).toFixed(2)}: `
                    + `${j.steps} step${j.steps > 1 ? 's' : ''} re-queued `
                    + `(${j.from} -> ${j.to} left). Route length unchanged.`}</title>
                </g>
              ))}

              {/* Scrapped: the line just ends. No projection, because it is not
                  going to complete. */}
              {l.state === 'scrapped' && l.points.length > 0 && (() => {
                const last = l.points[l.points.length - 1]
                const cx = view.x(last.t)
                const cy = view.y(metric === 'steps' ? last.left : last.rem_s)
                const r = 5
                return (
                  <g>
                    <line x1={cx - r} y1={cy - r} x2={cx + r} y2={cy + r}
                          stroke="#b91c1c" strokeWidth="2" />
                    <line x1={cx - r} y1={cy + r} x2={cx + r} y2={cy - r}
                          stroke="#b91c1c" strokeWidth="2" />
                    <title>{`${l.lot} scrapped at d${(last.t / 86400).toFixed(2)}`}</title>
                  </g>
                )
              })()}
            </g>
          )
        })}

        {/* Where warm-up ends and this run begins. */}
        {view && view.warm != null && view.warm > view.d0 && view.warm < view.d1 && (
          <>
            <line x1={view.x(view.warm)} x2={view.x(view.warm)}
                  y1={M.t} y2={M.t + ih}
                  stroke={HISTORIC} strokeWidth="1.5" strokeOpacity="0.6" />
            <text x={view.x(view.warm) - 4} y={M.t + 10} fontSize="10"
                  textAnchor="end" fill={HISTORIC}>sim start</text>
          </>
        )}

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

      <LotStats lots={view ? view.lots : []} focus={focus} now={view && view.now} />

      <div className="burndown-legend">
        {Object.entries(REASON).map(([k, v]) => (
          <span key={k}><i style={{ background: v.color }} />{v.label}</span>
        ))}
        <span><i style={{ background: '#7c3aed', opacity: 0.3 }} />batch step (observed)</span>
        <span><i style={{ background: HISTORIC }} />warm-up (before sim start)</span>
        <span><i style={{ background: '#6b7280' }} />projected (naive)</span>
        <span><i style={{ background: '#dc2626', borderRadius: '50%' }} />due date (on zero line)</span>
        <span><i style={{ background: '#b45309' }} />rework jog</span>
        <span><i style={{ background: '#b91c1c' }} />scrapped (&times;)</span>
        <span className="muted">coloured dash = required rate · vertical dash = due date</span>
      </div>

      <p className="muted burndown-note">
        Steps are not equal work: forty metrology steps and forty implant steps
        are very different amounts of cycle time, so step count flatters lots
        with a long tail of quick operations. <b>process time left</b> sums the
        raw process time over the remaining route — it excludes queue time, which
        the simulator does not forecast, so it is a floor rather than a predicted
        finish. The line is <b>not monotonic</b>: rework splices completed steps
        back onto the route and the burndown goes up. That jog is real: the lot
        has gone back in the line and must redo those steps, so the number
        <i> remaining</i> rises while the <i>total</i> route length is
        unchanged. It is not extra work added to the route.
      </p>
      <p className="muted burndown-note">
        The <b>gray dashed ray</b> is a naive projection: steps left multiplied
        by the median seconds-per-step this product and lot type have actually
        achieved, started from now rather than from the lot's last move so a
        stalled lot is not forgiven its wait. It assumes no further rework, no
        tool downtime and unchanged queueing, so it is a reference line, not a
        forecast. The <b>vertical dash</b> is the due date: a ray crossing zero
        to the right of it is projected late. A <b>scrapped</b> lot ends in a
        red &times; and gets no projection at all.
      </p>
      <p className="muted burndown-note">
        Lines drawn in <b>black</b> are <b>warm-up</b>: the fab was simulated for
        several days before this run started, so an active lot already has a
        past. That history is captured during warm-up and published on the
        compacted state topic, which is why it survives an API restart. It is
        decimated to at most 60 points per lot, keeping the endpoints and every
        rework jog. Everything from the <b>sim start</b> rule rightwards happened
        during the run you are watching. A warm-up stall is not something this
        run caused, which is the reason the two eras are not drawn in the same
        ink.
      </p>
    </div>
    </section>
    </>
  )
}

/**
 * Summary for the focused lot, or for the whole cohort when nothing is focused.
 *
 * Every number here comes from the server's `stats` and `projection` blocks
 * rather than being recomputed in the browser. Two derivations of the same
 * quantity drift, and then the table and the chart disagree about the same lot
 * with nothing to say which is right.
 */
function LotStats({ lots, focus, now }) {
  if (!lots.length) return null
  const sel = focus ? lots.filter(l => l.lot === focus) : lots
  if (!sel.length) return null

  const one = sel.length === 1 ? sel[0] : null
  const sum = k => sel.reduce((a, l) => a + (l.stats?.[k] ?? 0), 0)
  const proj = sel.map(l => l.projection).filter(Boolean)
  const late = sel.filter(l => l.projection && l.projection.slack_s != null
                               && l.projection.slack_s < 0)
  const worst = proj.length
    ? proj.reduce((a, b) => (a.slack_s ?? 0) <= (b.slack_s ?? 0) ? a : b)
    : null

  const cells = one
    ? [
        ['lot', one.lot],
        ['product', `${one.part}${one.hot ? ' · HOT' : ''}`],
        ['state', one.state],
        ['progress', `${one.stats.steps_done} / ${one.stats.route} steps `
                     + `(${one.stats.pct_complete ?? '—'}%)`],
        ['steps left', one.stats.steps_left],
        ['rework', one.stats.rework_events
            ? `${one.stats.rework_events} ×, ${one.stats.rework_steps} steps re-queued`
            : 'none'],
        ['idle since last move', fmtDur(one.stats.idle_s)],
        ['queue / batch / process',
          `${fmtDur(one.stats.queue_s)} / ${fmtDur(one.stats.batch_wait_s)} / `
          + `${fmtDur(one.stats.process_s)}`],
        ['rate used', one.projection
            ? `${(one.projection.rate_s / 86400).toFixed(3)} d/step `
              + `(${one.projection.basis}, n=${one.projection.n})`
            : '—'],
        ['projected finish', one.projection
            ? `d${(one.projection.eta_t / 86400).toFixed(1)}` : '—'],
        ['due', one.due ? `d${(one.due / 86400).toFixed(1)}` : 'none'],
        ['slack (due − projected)', one.projection
            ? fmtSigned(one.projection.slack_s) : '—'],
      ]
    : [
        ['lots selected', sel.length],
        ['products', [...new Set(sel.map(l => l.part))].join(', ')],
        ['steps left (min / max)',
          `${Math.min(...sel.map(l => l.stats.steps_left))} / `
          + `${Math.max(...sel.map(l => l.stats.steps_left))}`],
        ['spread', Math.max(...sel.map(l => l.stats.steps_left))
                   - Math.min(...sel.map(l => l.stats.steps_left))],
        ['rework', `${sum('rework_events')} ×, ${sum('rework_steps')} steps re-queued`],
        ['scrapped', sel.filter(l => l.state === 'scrapped').length],
        ['done', sel.filter(l => l.state === 'done').length],
        ['projected late', `${late.length} of ${proj.length} projected`],
        ['worst slack', worst ? fmtSigned(worst.slack_s) : '—'],
      ]

  return (
    <div className="burndown-stats">
      <h4>{one ? 'Selected lot' : `Cohort — ${sel.length} lots`}</h4>
      <table className="tbl">
        <tbody>
          {cells.map(([k, v]) => (
            <tr key={k}><th>{k}</th><td>{String(v)}</td></tr>
          ))}
        </tbody>
      </table>
      {!one && (
        <p className="muted">Click a lot in <b>lots</b> mode to drill in.</p>
      )}
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
