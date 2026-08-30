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
  const { iw, ih, y, yMax } = frame(w, h, series, pad)
  const slot = iw / Math.max(1, cap - 1)
  // Right-anchored: index n-1 lands on the right edge whatever n is, so the
  // series grows leftwards out of the edge instead of stretching to fit.
  const x = i => M.l + iw - (n - 1 - i) * slot
  return { mode: 'index', n, cap, slot, iw, ih, x, y, yMax }
}

/**
 * The same plot with x in *simulated* time rather than in samples.
 *
 * The WIP series is fab data, and the fab's clock is not the browser's: the
 * feed replays at 1x to 400x and can be paused, so equal wall-clock spacing
 * between arrivals is not equal fab time. Plotted by sample index, a window
 * covers ten minutes of fab at 1x and nearly three days at 400x while looking
 * identical, a slope means 400x different things depending on a control in the
 * header, and a paused feed keeps scrolling out a flat line that reads as
 * steady WIP rather than as a stopped fab.
 *
 * Against sim time all three go away by construction. The newest sample still
 * anchors the right edge, but distance is elapsed fab time, so a speed change
 * shows up as the sample spacing changing rather than as a silently rescaled
 * axis, and a pause advances the clock by nothing and the chart holds still --
 * which is exactly what happened.
 *
 * @param {number[]} ts    sim time per sample (seconds), ascending
 * @param {number}   span  sim seconds visible across the plot
 */
export function timeView(ts, span, w, h, series, pad = 0.1) {
  const { iw, ih, y, yMax } = frame(w, h, series, pad)
  const n = ts.length
  const t1 = n ? ts[n - 1] : 0
  const s = span > 0 ? span : 1
  const xAt = t => M.l + iw - ((t1 - t) / s) * iw
  return {
    mode: 'time', n, ts, span: s, t0: t1 - s, t1, iw, ih, y, yMax, xAt,
    x: i => xAt(ts[i]),
    // One sample's worth of x, for the empty-window and label-spacing cases.
    slot: n > 1 ? Math.max(1e-6, xAt(ts[n - 1]) - xAt(ts[n - 2])) : iw,
  }
}

/** Plot box and the shared y scale. */
function frame(w, h, series, pad) {
  const iw = Math.max(1, w - M.l - M.r)
  const ih = Math.max(1, h - M.t - M.b)
  const vals = series.flat().filter(v => Number.isFinite(v))
  const peak = vals.length ? Math.max(...vals) : 0
  // Zero-based, and rounded up to a "nice" step. Not cosmetic: an axis that
  // retracks to the exact peak moves the whole line vertically on every frame,
  // which reintroduces the jitter the fixed x slot just removed. A stepped
  // domain holds still across many samples and moves once, visibly.
  const yMax = niceMax(peak * (1 + pad), TICKS)
  const y = v => M.t + ih - (Math.max(0, Math.min(v, yMax)) / (yMax || 1)) * ih
  return { iw, ih, y, yMax }
}

/**
 * How much fab time to show, measured rather than taken from the speed dial.
 *
 * `cap` samples at the rate fab time is currently arriving. Derived from the
 * last real advance instead of from speed x heartbeat because that reading is
 * true whatever is actually happening -- a throttled feed, an unpaced run, a
 * heartbeat that slipped -- and because it is the same discipline the link
 * metrics use: report what arrived, not what was configured.
 *
 * A pause advances nothing, so it holds the span it had; without that the
 * window would collapse to zero the moment someone hit pause.
 */
export function spanFor(ts, cap, prev = 0) {
  for (let i = ts.length - 1; i > 0; i--) {
    const d = ts[i] - ts[i - 1]
    if (d > 0) return d * Math.max(1, cap - 1)
  }
  return prev > 0 ? prev : 1
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
  if (v.mode === 'index') {
    const i = Math.round(v.n - 1 - (M.l + v.iw - px) / v.slot)
    return i < 0 || i > v.n - 1 ? null : i
  }
  // Time mode has no constant slot to divide by -- samples are spaced by fab
  // time, which is the point -- so the nearest one is found by scanning.
  let best = null, bestD = Infinity
  for (let i = 0; i < v.n; i++) {
    const d = Math.abs(v.x(i) - px)
    if (d < bestD) { bestD = d; best = i }
  }
  // Beyond half the visible span from any sample there is nothing to read.
  return bestD > Math.max(v.slot, 12) ? null : best
}

/**
 * Tick indices, newest first, spaced so labels do not collide.
 *
 * Walked from the newest backwards so the rightmost sample -- the one being
 * read -- is always labelled, whatever the spacing works out to.
 */
export function xTickIndices(v, minGapPx = 70) {
  if (v.mode === 'index') {
    const every = Math.max(1, Math.ceil(minGapPx / v.slot))
    const out = []
    for (let i = v.n - 1; i >= 0; i -= every) out.push(i)
    return out.reverse()
  }
  const out = []
  let lastX = Infinity
  for (let i = v.n - 1; i >= 0; i--) {
    const x = v.x(i)
    if (lastX - x >= minGapPx || out.length === 0) { out.push(i); lastX = x }
  }
  return out.reverse()
}

/** `d12.4`, the same fab-clock label the burndown uses. */
export const fmtSimTime = t => `d${(t / 86400).toFixed(1)}`

/** A sim-time duration in the largest unit that stays readable. */
export function fmtSpan(s) {
  if (!(s > 0)) return '—'
  if (s >= 86400) return `${(s / 86400).toFixed(s >= 864000 ? 0 : 1)} fab days`
  if (s >= 3600) return `${(s / 3600).toFixed(1)} fab hours`
  if (s >= 60) return `${Math.round(s / 60)} fab min`
  return `${Math.round(s)} fab s`
}

/**
 * How far the plot group should start offset, in px, for the step just taken.
 *
 * Zero means "do not animate, just draw": the cases where a translate would be
 * a lie rather than a slide. Kept here, and not in the component, because
 * every one of them is a geometry question and none of them is observable
 * from a rendered chart -- a wrong answer looks like a chart that flies in
 * from the side once and is never seen again.
 *
 * @param {{at:number, span:number}} was  previous anchor and window
 * @param {{at:number, span:number}} now  current anchor and window
 * @param {object} v    the view being drawn
 * @param {boolean} timed  true in sim-time mode
 */
export function travel(was, now, v, timed) {
  if (!v || v.n < 2) return 0
  // A rescaled window (playback speed changed) is a new axis, not a step along
  // the old one: distance either side of it is not comparable, so it snaps.
  if (now.span !== was.span) return 0
  // Backwards only happens on a new run; the clock is otherwise monotonic.
  if (now.at <= was.at) return 0
  const px = timed ? ((now.at - was.at) / v.span) * v.iw : v.slot
  // Further than the whole window is a reconnect after a gap, not a slide.
  return px > 0 && px <= v.iw ? px : 0
}
