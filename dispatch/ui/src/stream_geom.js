/**
 * Geometry for the live streaming line charts, split out of the component for
 * the same reason as avail_geom.js: the scale is where a chart lies, and these
 * two are the charts people watch while the fab is running.
 *
 * The defining property here is that x is *right-anchored*: the newest sample
 * always sits on the right edge and every older one is a fixed slot width to
 * its left. A rolling window drawn any other way rescales x on every frame, so
 * every point in the series moves a little whenever one arrives -- which is
 * what makes a redrawn chart look like it is squirming rather than scrolling.
 * With a fixed slot the whole series translates by exactly one slot per
 * sample, so the component can animate that single translation and the chart
 * scrolls the way a strip recorder does.
 */

export const M = { t: 8, r: 10, b: 18, l: 44 }

/**
 * Build a scale for `n` samples in a window of `cap` slots.
 *
 * @param {number} n    samples held
 * @param {number} cap  window size in samples; the plot is cap-1 slots wide
 * @param {number} w    plot width in px
 * @param {number} h    plot height in px
 * @param {number[][]} series  one array of values per line, for the y domain
 * @param {number} pad  fraction of headroom above the peak
 */
export function view(n, cap, w, h, series, pad = 0.1) {
  const iw = Math.max(1, w - M.l - M.r)
  const ih = Math.max(1, h - M.t - M.b)
  const slot = iw / Math.max(1, cap - 1)
  // Right-anchored: index n-1 lands on the right edge whatever n is, so the
  // series grows leftwards out of the edge instead of stretching to fit.
  const x = i => M.l + iw - (n - 1 - i) * slot
  const vals = series.flat().filter(v => Number.isFinite(v))
  const peak = vals.length ? Math.max(...vals) : 0
  // Zero-based, and rounded up to a "nice" step. Not cosmetic: an axis that
  // retracks to the exact peak moves the whole line vertically on every frame,
  // which reintroduces the jitter the fixed x slot just removed. A stepped
  // domain holds still across many samples and moves once, visibly.
  const yMax = niceMax(peak * (1 + pad), TICKS)
  const y = v => M.t + ih - (Math.max(0, Math.min(v, yMax)) / (yMax || 1)) * ih
  return { n, cap, slot, iw, ih, x, y, yMax }
}

export const TICKS = 4

/**
 * Smallest domain at or above v that divides into `ticks` nice steps.
 *
 * Rounding the *step* rather than the top matters: rounding the top straight
 * onto the 1/2/5 ladder makes the axis double (50 -> 100) when the series
 * creeps a few percent, and half the plot goes empty in one frame for no
 * reason a reader can see. Stepping gives the same stability with a fifth of
 * the jump, and every gridline still lands on a round number.
 */
export function niceMax(v, ticks = TICKS) {
  if (!(v > 0)) return ticks
  return niceStep(v / ticks) * ticks
}

/** Smallest 1/2/5 x 10^k at or above v. */
export function niceStep(v) {
  if (!(v > 0)) return 1
  const mag = 10 ** Math.floor(Math.log10(v))
  const f = v / mag
  return (f <= 1 ? 1 : f <= 2 ? 2 : f <= 5 ? 5 : 10) * mag
}

/** Evenly spaced y ticks including 0 and yMax. */
export function yTicks(v, count = TICKS) {
  return Array.from({ length: count + 1 }, (_, i) => (v.yMax * i) / count)
}

export function points(v, series) {
  const out = []
  for (let i = 0; i < series.length; i++) {
    const s = series[i]
    if (Number.isFinite(s)) out.push(`${v.x(i).toFixed(1)},${v.y(s).toFixed(1)}`)
  }
  return out.join(' ')
}

/** Index of the sample nearest a plot-space x, or null when off the series. */
export function indexAt(v, px) {
  if (!v.n) return null
  const i = Math.round(v.n - 1 - (M.l + v.iw - px) / v.slot)
  if (i < 0 || i > v.n - 1) return null
  return i
}

/** Tick indices, newest first, spaced so labels do not collide. */
export function xTickIndices(v, minGapPx = 70) {
  const every = Math.max(1, Math.ceil(minGapPx / v.slot))
  const out = []
  for (let i = v.n - 1; i >= 0; i -= every) out.push(i)
  return out.reverse()
}
