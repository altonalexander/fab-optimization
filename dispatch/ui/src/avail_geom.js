// Geometry for the tool-availability strip, split out of the component so it
// can be tested without a DOM -- same split as burndown_geom.js, for the same
// reason: the scale is where a chart lies, and a lying availability chart is
// worse than no chart.

export const W = 900, H = 92
export const M = { t: 10, r: 56, b: 16, l: 40 }

/**
 * Build the scale and the two series.
 *
 * @param {number[]} ts     sample timestamps (seconds)
 * @param {number[]} online tools online at each sample
 * @param {number[]} total  roster size at each sample
 * @param {number}   now    current roster size, so the reference line is on
 *                          screen even before the series has caught up
 */
export function view(ts, online, total, now) {
  const n = ts.length
  if (!n) return null
  const iw = W - M.l - M.r
  const ih = H - M.t - M.b
  // Zero-based, always. An axis starting at the series minimum turns a 2% dip
  // into a visual collapse, which is the single easiest way to misread an
  // availability chart. yMax is the roster, so the reference line is the top
  // of the plot and the gap beneath it is the outage, to scale.
  const yMax = Math.max(now || 0, ...total, 1)
  const y = v => M.t + ih - (Math.max(0, Math.min(v, yMax)) / yMax) * ih
  // A single sample has no span to interpolate across; pin it to the right
  // edge, where the newest point always lives.
  const x = i => n < 2 ? M.l + iw : M.l + (i / (n - 1)) * iw
  return { n, yMax, x, y, iw, ih }
}

export function points(v, series) {
  return series.map((s, i) => `${v.x(i).toFixed(1)},${v.y(s).toFixed(1)}`).join(' ')
}

export function areaPath(v, series) {
  if (v.n < 2) return null
  return `M${v.x(0).toFixed(1)},${v.y(0).toFixed(1)} L${points(v, series)}` +
         ` L${v.x(v.n - 1).toFixed(1)},${v.y(0).toFixed(1)} Z`
}

export function fmtClock(ts) {
  const d = new Date(ts * 1000)
  return `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`
}
