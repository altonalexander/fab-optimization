import { useEffect, useState } from 'react'
import { W, H, M, view, points, areaPath, fmtClock } from './avail_geom.js'

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
  const v = view(d.ts, d.online, d.total, total)
  const n = v ? v.n : 0

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
          {n > 1 && <span>{fmtClock(d.ts[0])}–{fmtClock(d.ts[n - 1])}</span>}
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
          <line x1={M.l} x2={W - M.r} y1={v.y(0)} y2={v.y(0)} stroke="#e5e7eb" />
          <path d={areaPath(v, d.online)} fill="#2563eb" fillOpacity="0.10" />
          {/* The roster itself moves as tools announce themselves, so the
              reference is a series too, not one flat line at today's total. */}
          <polyline points={points(v, d.total)} fill="none" stroke="#6b7280"
                    strokeWidth="1.5" strokeDasharray="5 4" />
          <polyline points={points(v, d.online)} fill="none" stroke={stroke}
                    strokeWidth="2" strokeLinejoin="round" />
          <text x={W - M.r + 6} y={v.y(v.yMax) + 4} fontSize="11" fill="#6b7280">
            {v.yMax.toLocaleString()} total
          </text>
          <text x={W - M.r + 6} y={v.y(d.online[n - 1]) + 4} fontSize="11"
                fill={stroke} fontWeight="600">
            {d.online[n - 1].toLocaleString()}
          </text>
          <text x={M.l - 6} y={v.y(0) + 4} textAnchor="end" fontSize="10"
                fill="#6b7280">0</text>
        </svg>
      )}
    </div>
  )
}
