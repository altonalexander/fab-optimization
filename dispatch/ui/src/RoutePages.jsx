import { useEffect, useMemo, useState } from 'react'

/**
 * Routes — the product route index and one page per product.
 *
 * This replaces the framed static route-explorer.html. The explorer was a
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

      {/* The page these views replaced. It is still built and still served, so
          it stays linked rather than orphaned: it draws all ten routes as one
          diagram, which is the one thing per-product pages cannot do. */}
      <p className="muted" style={{ fontSize: 11, marginTop: 14 }}>
        All ten routes on one diagram:{' '}
        <a href="/route-explorer.html" target="_blank" rel="noreferrer">
          route-explorer.html
        </a>{' '}
        (static, built by <code>viz/build_route_diagram.py</code>)
      </p>
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
  const [order, setOrder] = useState([])
  const [err, setErr] = useState(null)

  useEffect(() => {
    setData(null); setErr(null)
    fetch(`/api/routes/${encodeURIComponent(product)}?limit=8`)
      .then(r => (r.ok ? r.json() : r.json().then(j => Promise.reject(j.error))))
      .then(setData)
      .catch(e => setErr(String(e || `could not load ${product}`)))
  }, [product])

  // The global area order lives on the index, and it is what keeps colours
  // stable between product pages. Fetched separately so a deep link straight
  // to #/routes/part_3 still gets it.
  useEffect(() => {
    fetch('/api/routes').then(r => r.json()).then(d => setOrder(d.areas || []))
      .catch(() => {})
  }, [])

  if (err) {
    return (
      <div>
        <p className="muted">{err}</p>
        <p><a href={backHref}>← all routes</a></p>
      </div>
    )
  }
  if (!data) return <p className="muted">loading {product}…</p>
  return <RouteProductView data={data} order={order} backHref={backHref}
                           cohortHref={cohortHref} />
}

// `order` is the global area order from the index; empty is fine, and falls
// back to this route's own areas so a page still renders if the index request
// is the one that failed.
export function RouteProductView({ data, order = [], backHref, cohortHref }) {
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
        <RStat label="sampled steps" value={data.sampled}
               sub="not every lot runs these" />
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
          Every visit in order, left to right, each block as wide as the steps
          it holds. The repeating colour pattern is the re-entrancy: the same
          few bays, over and over, for {data.n_visits} visits.
        </p>
        <VisitStrip visits={data.visits} color={color} total={data.n_steps} />
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

// The whole route as one strip. Rendered as flex spans rather than SVG rects:
// 400+ blocks need a tooltip and a sane wrap, and the browser's own layout
// does both for free.
function VisitStrip({ visits, color, total }) {
  return (
    <div className="visit-strip" role="img"
         aria-label={`${visits.length} visits across the route, in order`}>
      {visits.map((v, i) => (
        <span key={i}
              style={{ flexGrow: v.steps, background: color(v.area) }}
              title={`visit ${i + 1}: ${v.area} — ${v.steps} step${
                v.steps === 1 ? '' : 's'} (${v.first}${
                v.last !== v.first ? `–${v.last}` : ''} of ${total})`} />
      ))}
    </div>
  )
}
