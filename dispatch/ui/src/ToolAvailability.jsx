import { useEffect, useState } from 'react'
import { W, H, M, view, points, areaPath, fmtClock } from './avail_geom.js'
import { fmtSimTime } from './stream_geom.js'

// Availability strip for the top of the tool index.
//
// The question it answers is "is the roster intact right now, and if not, is
// it recovering or still sliding?" -- which the group rows below cannot
// answer, because you would have to scan a few thousand cards to see a trend.
// The dashed reference line is the roster size: the series should be sitting
// on it, and the gap beneath it is the outage.
//
// Deliberately a sparkline, not a chart you drill into. It earns its place by
// being glanceable; the drill-down is the tool detail view.

export default function ToolAvailability() {
  const [d, setD] = useState(null)

  useEffect(() => {
    let live = true
    const load = () => fetch('/api/tools/availability').then(r => r.json())
      .then(j => live && setD(j)).catch(() => {})
    load()
    const iv = setInterval(load, 5000)
    return () => { live = false; clearInterval(iv) }
  }, [])

  if (!d || !d.now?.total) return null

  const { online: nOnline, total, down } = d.now
  const pct = 100 * nOnline / total
  // Fab time on the axis, like every other chart of fab data in the app: the
  // feed replays at 1x to 1600x and pauses, so wall spacing says nothing about
  // how much fab the strip covers. Samples taken before the clock was known
  // have no fab stamp and are dropped; the wall clock is the fallback only
  // when nothing is stamped at all.
  const stamped = (d.sim_t || []).map((t, i) => t != null ? i : -1).filter(i => i >= 0)
  const simAxis = stamped.length >= 2
  const pick = arr => simAxis ? stamped.map(i => arr[i]) : arr
  const ts = simAxis ? pick(d.sim_t) : d.ts
  const online = pick(d.online), totals = pick(d.total)
  const fmtT = simAxis ? fmtSimTime : fmtClock
  const v = view(ts, online, totals, total)
  const n = v ? v.n : 0
  const last = n ? online[n - 1] : 0

  const inferred = Object.entries(d.recovered || {})
  const state = down === 0 ? 'ok' : (down / total > 0.1 ? 'bad' : 'warn')
  const stroke = down ? '#b45309' : '#15803d'

  return (
    <div className="avail">
      <div className="avail-head">
        <div>
          <span className={`avail-pct avail-${state}`}>{pct.toFixed(1)}%</span>
          <span className="muted"> of {total.toLocaleString()} tools online</span>
        </div>
        <div className="avail-notes muted">
          {down > 0 && <span className="danger">{down.toLocaleString()} down</span>}
          {inferred.map(([how, k]) => (
            // Worth surfacing rather than hiding: these tools are shown online
            // because the mirror inferred it, not because the fab said so.
            <span key={how} title={how === 'watchdog'
              ? `down past the ${Math.round(d.ttl_s / 60)} min watchdog TTL with no status either way, assumed back up`
              : 'started a lot while marked down, so marked back up'}>
              {k} restored by {how}
            </span>
          ))}
          {n > 1 && (
            <span title={simAxis ? 'simulated fab days' : 'wall clock (fab clock not yet seen)'}>
              {fmtT(ts[0])}–{fmtT(ts[n - 1])}{simAxis ? ' fab time' : ' wall clock'}
            </span>
          )}
        </div>
      </div>

      {n < 2 ? (
        <div className="avail-empty muted">
          collecting… first points appear after {Math.round(d.sample_s * 2)}s
        </div>
      ) : (
        <svg viewBox={`0 0 ${W} ${H}`} className="avail-svg" role="img"
             preserveAspectRatio="none"
             aria-label={`${nOnline} of ${total} tools online`}>
          <line x1={M.l} x2={W - M.r} y1={v.y(v.yMin)} y2={v.y(v.yMin)}
                stroke="#e5e7eb" />
          <path d={areaPath(v, online)} fill="#2563eb" fillOpacity="0.10" />
          {/* The roster itself moves as tools announce themselves, so the
              reference is a series too, not one flat line at today's total. */}
          <polyline points={points(v, totals)} fill="none" stroke="#6b7280"
                    strokeWidth="1.5" strokeDasharray="5 4" />
          <polyline points={points(v, online)} fill="none" stroke={stroke}
                    strokeWidth="2" strokeLinejoin="round" />
          {/* now: where measurement stops. Everything right of it is empty
              because it has not happened -- the same rule the burndown and the
              live charts draw, so "right of the dashed line" means the same
              thing on every chart in the app. */}
          <line x1={v.nowX} x2={v.nowX} y1={M.t} y2={v.y(v.yMin)}
                stroke="#111827" strokeDasharray="3 3" />
          <text x={v.nowX + 4} y={M.t + 9} fontSize="9" fill="#111827">now</text>
          <text x={W - M.r + 6} y={v.y(v.yMax) + 4} fontSize="11" fill="#6b7280">
            {v.yMax.toLocaleString()} total
          </text>
          {/* Skipped when it would sit on top of the total label. At full
              availability the two coincide, and the header already states the
              number -- an overlapped pair of numbers is worse than one. */}
          {Math.abs(v.y(last) - v.y(v.yMax)) > 11 && (
            <text x={W - M.r + 6} y={v.y(last) + 4} fontSize="11"
                  fill={stroke} fontWeight="600">
              {last.toLocaleString()}
            </text>
          )}
          {/* The floor is labelled because the axis does NOT include zero --
              that window is what makes a 25-of-1,313 outage visible at all,
              and an unlabelled non-zero axis is the misleading version. */}
          <text x={M.l - 6} y={v.y(v.yMin) + 4} textAnchor="end" fontSize="10"
                fill="#6b7280">{v.yMin.toLocaleString()}</text>
        </svg>
      )}
    </div>
  )
}
