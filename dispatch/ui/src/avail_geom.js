// Geometry for the tool-availability strip, split out of the component so it
// can be tested without a DOM -- same split as burndown_geom.js, for the same
// reason: the scale is where a chart lies, and a lying availability chart is
// worse than no chart.

export const W = 900, H = 92
export const M = { t: 10, r: 56, b: 16, l: 40 }

// How far below the roster the axis reaches by default. A zero-based axis is
// the textbook choice, but at LVHM scale it is useless: 25 tools down out of
// 1,313 is 2% of the plot height, so the line pins to the top and every real
// movement is invisible. The window is what makes an outage legible. It is a
// fixed count rather than a percentage so the same vertical distance means the
// same number of tools whatever the fab size.
export const WINDOW = 100

// Share of the plot held open to the right of the newest sample, matching the
// streaming charts (stream_geom.js FUTURE) and the burndown's RIGHT_PAD_S.
// The last sample is *now*, and pinned against the frame there is no way to
// tell "caught up" from "cut off". The strip is also where a forecast would be
// drawn: expected recoveries live to the right of the rule, measurements to
// the left, and the rule is what keeps the two from being read as one series.
export const FUTURE = 0.12

/**
 * Build the scale and the two series.
 *
 * The y-axis spans [yMax - WINDOW, yMax] and does NOT include zero. That is a
 * deliberate trade: it makes ordinary variation readable at the cost of
 * exaggerating it, so the axis is labelled at both ends -- an unlabelled
 * non-zero axis is the actually misleading version.
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
  const yMax = Math.max(now || 0, ...total, 1)
  // The window is a floor on the range, not a clamp on the data: a big outage
  // pushes the axis down to keep the line on the plot rather than clipping it
  // flat against the bottom, which would hide exactly the event that matters
  // most. Never below zero -- a negative tool count is not a thing.
  const lowest = Math.min(...online, yMax)
  const yMin = Math.max(0, Math.min(yMax - WINDOW, lowest - 2))
  const span = Math.max(1, yMax - yMin)
  const y = v => M.t + ih -
    ((Math.max(yMin, Math.min(v, yMax)) - yMin) / span) * ih
  // Measurements occupy everything left of the future strip; `nowX` is where
  // the newest sample sits and the strip beyond it is deliberately empty.
  const dataW = iw * (1 - FUTURE)
  const nowX = M.l + dataW
  // A single sample has no span to interpolate across; pin it to the rule,
  // where the newest point always lives.
  const x = i => n < 2 ? nowX : M.l + (i / (n - 1)) * dataW
  return { n, yMax, yMin, span, x, y, iw, ih, nowX }
}

export function points(v, series) {
  return series.map((s, i) => `${v.x(i).toFixed(1)},${v.y(s).toFixed(1)}`).join(' ')
}

export function areaPath(v, series) {
  if (v.n < 2) return null
  // Closes on yMin, the bottom of the plot, not on zero -- zero is off the
  // axis now, so filling to it would run the shape off the bottom of the SVG.
  const base = v.y(v.yMin).toFixed(1)
  return `M${v.x(0).toFixed(1)},${base} L${points(v, series)}` +
         ` L${v.x(v.n - 1).toFixed(1)},${base} Z`
}

export function fmtClock(ts) {
  const d = new Date(ts * 1000)
  return `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`
}
