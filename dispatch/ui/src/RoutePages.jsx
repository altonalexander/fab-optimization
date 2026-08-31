import { useEffect, useMemo, useState } from 'react'

/**
 * Routes — the product route index and one page per product.
 *
 * This replaced the framed static route-explorer.html (since removed). The explorer was a
 * single 210KB page holding all ten routes at once, which meant it had no URL
 * for "the route part_3 runs": you could link to the explorer, never to a
 * product. Now every product is its own address (#/routes/part_3), so the lots
 * view can link a cohort straight to the route its lots are walking, and each
 * route page can link back to a sample of the cohorts on it.
 *
 * Data comes from /api/routes and /api/routes/<product>. The route itself is
 * static reference data; only the cohort sample is live.
 */

// One colour per process area, assigned from the index's `areas` list, which
// the API returns ordered by total steps across all routes. Assigning by that
// global order rather than per route keeps Wet_Etch the same colour on every
// product page -- comparing two routes by eye is the whole point of the strip.
const AREA_HUES = [
  '#2563eb', '#dc2626', '#059669', '#d97706', '#7c3aed', '#0891b2',
  '#be185d', '#4d7c0f', '#0369a1', '#9333ea', '#b45309', '#15803d',
  '#64748b',
]
const OTHER = '#cbd5e1'

function areaColors(areas) {
  const m = new Map()
  areas.forEach((a, i) => m.set(a, AREA_HUES[i % AREA_HUES.length]))
  return a => m.get(a) || OTHER
}

const pct = (n, d) => (d ? `${((100 * n) / d).toFixed(1)}%` : '—')
const fmtDay = t => (t == null ? '—' : `d${(t / 86400).toFixed(1)}`)

// ---------------------------------------------------------------------------
// Index
// ---------------------------------------------------------------------------

export function RouteIndex({ hrefFor }) {
  const [data, setData] = useState(null)
  const [err, setErr] = useState(null)

  useEffect(() => {
    fetch('/api/routes')
      .then(r => (r.ok ? r.json() : r.json().then(j => Promise.reject(j.error))))
      .then(setData)
      .catch(e => setErr(String(e || 'could not reach /api/routes')))
  }, [])

  if (err) return <p className="muted">{err}</p>
  if (!data) return <p className="muted">loading routes…</p>
  return <RouteIndexView data={data} hrefFor={hrefFor} />
}

// Split from the fetch above so it renders from a plain object -- which is
// what src/routepages.test.mjs drives, against the real routes JSON, with no
// browser and no server in the loop.
export function RouteIndexView({ data, hrefFor }) {
  const color = useMemo(() => areaColors(data.areas), [data])

  // Every bar is scaled to the longest route, not to its own total, so the
  // difference between a 242-step product and a 583-step one is visible as
  // length rather than hidden by per-row normalisation.
  const longest = Math.max(...data.products.map(p => p.n_steps), 1)

  return (
    <div className="routes-index">
      <p className="muted" style={{ marginTop: -4 }}>
        Ten saleable products, one route each, from <code>{data.dataset}</code>.
        A route is a step list; the bar is where those steps are spent, by
        process area. Re-entrancy is why the routes are long: a lot returns to
        litho and etch dozens of times, so <b>visits</b> (consecutive steps in
        one bay, collapsed) is always well below <b>steps</b>.
      </p>

      <div className="route-cards">
        {data.products.map(p => (
          <a key={p.product} className="route-card" href={hrefFor(p.product)}>
            <div className="route-card-head">
              <h4>{p.product}</h4>
              <code className="muted">{p.route}</code>
            </div>
            <div className="route-card-stats">
              <span><b>{p.n_steps}</b> steps</span>
              <span><b>{p.n_visits}</b> visits</span>
              <span><b>{p.n_areas}</b> areas</span>
            </div>
            <AreaBar areas={p.areas} order={data.areas} color={color}
                     total={longest} />
            <div className="route-card-foot muted">
              <span>most time in <b>{p.dominant_area}</b></span>
              {p.cohorts_tracked
                ? <span className="route-live">
                    {p.cohorts_tracked} cohort{p.cohorts_tracked === 1 ? '' : 's'} ·{' '}
                    {p.lots_tracked} lots live
                  </span>
                : <span className="muted">no lots tracked</span>}
            </div>
          </a>
        ))}
      </div>

      <Legend areas={data.areas} color={color} />

    </div>
  )
}

// Stacked steps-per-area bar. `total` is the scale denominator: pass the row's
// own total to fill the width, or the longest route's to compare across rows.
function AreaBar({ areas, order, color, total }) {
  const own = Object.values(areas).reduce((a, b) => a + b, 0)
  const denom = total || own
  const segs = order.filter(a => areas[a])
  return (
    <div className="area-bar" role="img"
         aria-label={segs.map(a => `${a} ${areas[a]} steps`).join(', ')}>
      {segs.map(a => (
        <span key={a} style={{ width: `${(100 * areas[a]) / denom}%`,
                               background: color(a) }}
              title={`${a} — ${areas[a]} steps (${pct(areas[a], own)})`} />
      ))}
    </div>
  )
}

function Legend({ areas, color }) {
  return (
    <div className="route-legend">
      {areas.map(a => (
        <span key={a}>
          <i style={{ background: color(a) }} />{a}
        </span>
      ))}
    </div>
  )
}

// ---------------------------------------------------------------------------
// One product
// ---------------------------------------------------------------------------

export function RouteProduct({ product, backHref, cohortHref }) {
  const [data, setData] = useState(null)
  const [err, setErr] = useState(null)

  useEffect(() => {
    setData(null); setErr(null)
    fetch(`/api/routes/${encodeURIComponent(product)}?limit=8`)
      .then(r => (r.ok ? r.json() : r.json().then(j => Promise.reject(j.error))))
      .then(setData)
      .catch(e => setErr(String(e || `could not load ${product}`)))
  }, [product])

  if (err) {
    return (
      <div>
        <p className="muted">{err}</p>
        <p><a href={backHref}>← all routes</a></p>
      </div>
    )
  }
  if (!data) return <p className="muted">loading {product}…</p>
  // `areas_order` is the global order the index sorts by, so a bay keeps its
  // colour between product pages; `zones` is the lane grouping for the map.
  return <RouteProductView data={data} order={data.areas_order}
                           zones={data.zones} backHref={backHref}
                           cohortHref={cohortHref} />
}

// `order` is the global area order from the index; empty is fine, and falls
// back to this route's own areas so a page still renders if the index request
// is the one that failed.
export function RouteProductView({ data, order = [], zones = [], backHref,
                                   cohortHref }) {
  const color = useMemo(() => areaColors(order), [order])
  const areas = order.length ? order : Object.keys(data.areas)
  const maxSteps = Math.max(...Object.values(data.areas), 1)

  return (
    <div className="route-detail">
      <div className="route-detail-head">
        <a href={backHref}>← all routes</a>
        <h3>{data.product} <code className="muted">{data.route}</code></h3>
      </div>

      <div className="stats-row">
        <RStat label="steps" value={data.n_steps} sub="route positions" />
        <RStat label="visits" value={data.n_visits}
               sub="consecutive steps collapsed" />
        <RStat label="areas" value={data.n_areas} sub={data.dominant_area} />
        <RStat label="batch steps" value={data.batch_steps}
               sub="furnace, per-batch" />
        <RStat label="rework loops" value={data.rework.length}
               sub="steps that can send a lot back" />
        <RStat label="measurement" value={data.measure_steps}
               sub={`${data.sampled} sampled`} />
      </div>

      <section>
        <h4>Where the steps go</h4>
        <p className="muted" style={{ marginTop: -4 }}>
          Steps are route positions; visits are arrivals at the bay. A bay with
          many more steps than visits is one a lot settles into; many visits per
          step is re-entrancy — the lot keeps coming back.
        </p>
        <table className="route-areas">
          <thead>
            <tr><th>area</th><th>steps</th><th></th><th>share</th>
                <th>visits</th><th>steps/visit</th></tr>
          </thead>
          <tbody>
            {areas.filter(a => data.areas[a]).map(a => (
              <tr key={a}>
                <td><i className="swatch" style={{ background: color(a) }} />{a}</td>
                <td className="num">{data.areas[a]}</td>
                <td className="bar-cell">
                  <span className="bar" style={{
                    width: `${(100 * data.areas[a]) / maxSteps}%`,
                    background: color(a),
                  }} />
                </td>
                <td className="num muted">{pct(data.areas[a], data.n_steps)}</td>
                <td className="num">{data.area_visits[a] || 0}</td>
                <td className="num muted">
                  {data.area_visits[a]
                    ? (data.areas[a] / data.area_visits[a]).toFixed(1)
                    : '—'}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>

      <section>
        <h4>The route, end to end</h4>
        <p className="muted" style={{ marginTop: -4 }}>
          One lane per process area, one column per step: a block marks where
          that step runs. The lane says which bay, so colour is free to say what
          the step <i>does</i> — plain process, measurement, or a measurement
          that can send the lot backwards. Reading across, the same few lanes
          fire over and over for {data.n_visits} visits: that is the
          re-entrancy, and it is why a {data.n_steps}-step route needs only{' '}
          {data.n_areas} bays.
        </p>
        <LaneMap data={data} zones={zones} />
        <LaneLegend data={data} />
        {data.rework.length > 0 && (
          <p className="muted" style={{ fontSize: 11, marginTop: 8 }}>
            Every rework point on this route is a{' '}
            <b>{data.rework[0].area}</b> step — the lot is measured after
            lithography, and a bad exposure goes back{' '}
            {data.rework[0].at - data.rework[0].back_to + 1} steps to be
            stripped and re-exposed. Nothing else on the route can send a lot
            backwards.
          </p>
        )}
      </section>

      {data.rework.length > 0 && (
        <section>
          <h4>Rework loops</h4>
          <p className="muted" style={{ marginTop: -4 }}>
            A lot failing at these steps is sent back to an earlier one, and the
            steps in between are re-run. This is why remaining-steps on the lots
            view is not monotonic.
          </p>
          <table className="route-areas">
            <thead>
              <tr><th>at step</th><th>area</th><th>back to</th>
                  <th>steps re-run</th><th>rate</th></tr>
            </thead>
            <tbody>
              {data.rework.map(r => (
                <tr key={r.at}>
                  <td className="num">{r.at}</td>
                  <td><i className="swatch" style={{ background: color(r.area) }} />
                      {r.area}</td>
                  <td className="num">{r.back_to ?? '—'}</td>
                  <td className="num">
                    {r.back_to ? r.at - r.back_to + 1 : '—'}
                  </td>
                  <td className="num">{r.pct}%</td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      )}

      <section>
        <h4>Cohorts on this route</h4>
        <p className="muted" style={{ marginTop: -4 }}>
          A cohort is one day of this product's releases — the lots that can
          batch together, since an SMT2020 furnace batch needs the same product{' '}
          <i>and</i> the same step. Every lot below walks the {data.n_steps}{' '}
          steps above.{' '}
          {data.total_cohorts > data.cohorts.length &&
            <>Showing {data.cohorts.length} of {data.total_cohorts}, most
               recently moved first.</>}
        </p>
        {data.cohorts.length === 0 ? (
          <p className="muted">
            No lots of {data.product} are being tracked. The cohort sample is
            fed by <code>LOT_PROGRESS</code> events from{' '}
            <code>bench/tools/sim_feed.py</code>; the route above is static and
            does not need the feed.
          </p>
        ) : (
          <table className="route-areas route-cohorts">
            <thead>
              <tr><th>cohort</th><th>lots</th><th>done</th>
                  <th>steps left (min–med–max)</th><th>spread</th>
                  <th>released</th><th>due</th></tr>
            </thead>
            <tbody>
              {data.cohorts.map(c => (
                <tr key={c.cohort}>
                  <td><a href={cohortHref(c.cohort)}>{c.cohort}</a></td>
                  <td className="num">{c.lots}</td>
                  <td className="num">{c.done || '—'}</td>
                  <td className="num">
                    {c.min_left} – {c.med_left} – {c.max_left}
                  </td>
                  {/* Spread is the number worth scanning: a cohort whose
                      fastest and slowest lot are far apart has already lost
                      the batch it was released to form. */}
                  <td className={c.spread > 0 ? 'num danger' : 'num'}>
                    {c.spread}
                  </td>
                  <td className="num muted">{fmtDay(c.release)}</td>
                  <td className="num muted">{fmtDay(c.due)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>
    </div>
  )
}

function RStat({ label, value, sub }) {
  return (
    <div className="stat">
      <div className="stat-label">{label}</div>
      <div className="stat-value">{value}</div>
      {sub && <div className="stat-sub">{sub}</div>}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Lane map — the route read left to right, one lane per process area.
//
// Identity comes from the lane, so colour is spent on what a block *is* rather
// than on naming its area again: process steps are neutral, measurement steps
// are marked, and rework points are the one accent on the page. That is the
// thing worth seeing here -- in LVHM every rework point sits on a litho
// metrology step, so the route's loops start exactly where it is measured.
// ---------------------------------------------------------------------------

const LANE_H = 22
const PAD = { l: 116, t: 10, r: 14, b: 34 }
const PROCESS = '#93a3b8'      // ordinary process step
const MEASURE = '#1d4ed8'      // measurement (a *_Met bay)
const SAMPLED = '#93b4f5'      // measurement run on only a fraction of lots
const REWORK = '#ea580c'       // the step that can send a lot backwards

const isMeasure = a => a.endsWith('_Met')

// Lanes in zone order, dropping the bays this route never visits. Falls back to
// the route's own areas if the grouping did not arrive.
function laneOrder(zones, areaVisits) {
  const flat = (zones || []).flatMap(z => z.areas)
  const present = flat.filter(a => areaVisits[a])
  const rest = Object.keys(areaVisits).filter(a => !flat.includes(a))
  return [...present, ...rest]
}

function visitTip(v, n, rw) {
  const span = v.last !== v.first ? `${v.first}–${v.last}` : `${v.first}`
  let t = `${v.area} — step ${span} of ${n}`
  if (v.steps > 1) t += ` (${v.steps} consecutive)`
  if (v.sampled) {
    t += `\nsampled: ${v.sampled} of ${v.steps} step` +
         `${v.steps === 1 ? '' : 's'} run on ${v.pct}% of lots`
  }
  if (rw) {
    t += `\nrework: ${rw.pct}% of lots go back to step ${rw.back_to}` +
         ` — ${rw.at - rw.back_to + 1} steps re-run`
  }
  return t
}

function LaneMap({ data, zones }) {
  const lanes = laneOrder(zones, data.area_visits || {})
  const n = data.n_steps || 1

  // Width is in step units, so one step is one unit wherever it lands. The SVG
  // scales to the panel and scrolls under a floor of ~1.6px per step, below
  // which a single-step visit stops being a visible mark at all.
  const cw = 1.9
  const iw = n * cw
  const W = PAD.l + iw + PAD.r
  const H = PAD.t + lanes.length * LANE_H + PAD.b
  const x = step => PAD.l + (step - 1) * cw
  const yOf = a => PAD.t + lanes.indexOf(a) * LANE_H

  // Rework is keyed by the step that triggers it so the visit containing that
  // step can be drawn in the accent rather than in its lane's own colour.
  const reworkAt = new Map((data.rework || []).map(w => [w.at, w]))
  const tickEvery = n > 400 ? 100 : n > 150 ? 50 : 25

  return (
    <div className="lane-scroll">
      <svg viewBox={`0 0 ${W} ${H}`} width={W} height={H}
           className="lane-map" role="img"
           aria-label={`Route map for ${data.product}: ${n} steps across ${
             lanes.length} process areas`}>
        {lanes.map((a, i) => {
          const y = yOf(a)
          const meas = isMeasure(a)
          return (
            <g key={a}>
              <rect x={PAD.l} y={y} width={iw} height={LANE_H - 4}
                    fill={i % 2 ? '#f1f5f9' : '#f8fafc'} />
              <text x={PAD.l - 10} y={y + LANE_H / 2 - 1} textAnchor="end"
                    dominantBaseline="middle" fontSize="10.5"
                    fill={meas ? MEASURE : '#4b5563'}
                    fontWeight={meas ? 600 : 400}>
                {a}
              </text>
              {/* Measurement lanes are called out on the axis itself: which
                  bays measure is the question this map is here to answer. */}
              {meas && (
                <text x={PAD.l - 10} y={y + LANE_H / 2 + 8.5} textAnchor="end"
                      dominantBaseline="middle" fontSize="8" fill="#9ca3af">
                  measure
                </text>
              )}
            </g>
          )
        })}

        {(data.visits || []).map((v, i) => {
          const y = yOf(v.area)
          if (y === undefined || lanes.indexOf(v.area) < 0) return null
          const rw = reworkAt.get(v.first) || reworkAt.get(v.last)
          // A one-step visit would otherwise be thinner than a hairline.
          const w = Math.max(cw * 0.9, v.steps * cw - 0.4)
          const meas = isMeasure(v.area)
          const fill = rw ? REWORK : !meas ? PROCESS
            : v.sampled === v.steps ? SAMPLED : MEASURE
          return (
            <rect key={i} x={x(v.first)} y={y + 2.5} width={w}
                  height={LANE_H - 9} rx="1.5" fill={fill}>
              {/* One string, not a fragment: an SVG <title> may hold exactly
                  one text node, and a browser renders the extra ones as
                  literal markup. */}
              <title>{visitTip(v, n, rw)}</title>
            </rect>
          )
        })}

        {/* Rework markers below the axis. The loop itself is only 2-3 steps
            long, so at full-route scale an arrow back would be invisible; a
            tick under the step is what actually reads. */}
        {(data.rework || []).map(w => (
          <polygon key={w.at} fill={REWORK}
                   points={`${x(w.at)},${PAD.t + lanes.length * LANE_H - 1} ${
                     x(w.at) - 3.5},${PAD.t + lanes.length * LANE_H + 5} ${
                     x(w.at) + 3.5},${PAD.t + lanes.length * LANE_H + 5}`}>
            <title>{`rework at step ${w.at} (${w.area}) — ${w.pct}% of lots ` +
                    `go back to step ${w.back_to}`}</title>
          </polygon>
        ))}

        {Array.from({ length: Math.floor(n / tickEvery) + 1 }, (_, i) => {
          const t = i * tickEvery
          return (
            <text key={t} x={x(t || 1)} y={H - PAD.b + 26} textAnchor="middle"
                  fontSize="9.5" fill="#9ca3af">
              {t || 1}
            </text>
          )
        })}
        <text x={PAD.l} y={H - 2} fontSize="9.5" fill="#9ca3af">
          process step →
        </text>
      </svg>
    </div>
  )
}

function LaneLegend({ data }) {
  const sampledPct = data.measure_steps
    ? Math.round((100 * data.sampled) / data.measure_steps) : 0
  return (
    <div className="route-legend lane-legend">
      <span><i style={{ background: PROCESS }} />process step</span>
      <span><i style={{ background: MEASURE }} />measurement, every lot</span>
      <span><i style={{ background: SAMPLED }} />measurement, sampled</span>
      <span><i style={{ background: REWORK }} />rework point</span>
      <span className="muted">
        {data.measure_steps} of {data.n_steps} steps measure
        {sampledPct ? `, ${sampledPct}% of those on a sample of lots` : ''}
      </span>
    </div>
  )
}
