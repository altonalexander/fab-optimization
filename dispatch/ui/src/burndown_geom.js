/**
 * Pure geometry for the cohort burndown, in data space (time, value).
 *
 * Kept out of the component and free of pixel scales so it can be exercised
 * directly against the API's JSON — see burndown_geom.test.mjs. The chart's
 * correctness lives almost entirely in here; the component only maps these
 * results through a linear scale and assigns colours.
 */

export const metricOf = (p, metric) => (metric === 'steps' ? p.left : p.rem_s)

/**
 * Step-after lookup: the value in force at time t is the value carried by the
 * last point at or before t. Returns null before the lot's first point.
 */
export function valueAt(points, t, metric) {
  if (!points.length || t < points[0].t) return null
  let lo = 0, hi = points.length - 1, best = 0
  while (lo <= hi) {
    const mid = (lo + hi) >> 1
    if (points[mid].t <= t) { best = mid; lo = mid + 1 } else { hi = mid - 1 }
  }
  return metricOf(points[best], metric)
}

/**
 * Step-after segments in data space.
 *
 * Each pair of consecutive points becomes a horizontal run (the wait) plus a
 * vertical move (the step completing). Linear interpolation between move-outs
 * would draw a gentle slope where reality is a flat wait then a sudden drop,
 * hiding queue time — which is most of the cycle. In a staircase the length of
 * the horizontal run *is* the wait, which is why runs carry the reason.
 *
 * The vertical move is signed: rework splices completed steps back onto the
 * route, so it can go up. Nothing here clamps that.
 */
export function segments(lot, metric, now) {
  const pts = lot.points || []
  if (!pts.length) return []
  const out = []
  for (let i = 1; i < pts.length; i++) {
    const a = pts[i - 1], b = pts[i]
    const va = metricOf(a, metric)
    // b.reason describes the run that ended when b arrived.
    out.push({ t1: a.t, v1: va, t2: b.t, v2: va, flat: true, reason: b.reason })
    out.push({ t1: b.t, v1: va, t2: b.t, v2: metricOf(b, metric), flat: false })
  }
  // A lot with no recent events has not finished — it is stuck. Extending the
  // flat run to now is the difference between "stuck for two days" and
  // "looks like it completed".
  const last = pts[pts.length - 1]
  if (lot.state !== 'done' && now > last.t) {
    const v = metricOf(last, metric)
    out.push({ t1: last.t, v1: v, t2: now, v2: v, flat: true,
               reason: last.reason, extended: true })
  }
  return out
}

/**
 * Min / median / max across the cohort on a uniform time grid.
 *
 * Band thickness is cohort spread, which is the thing worth seeing: a widening
 * band means the lots are desynchronising and will stall at the next batch
 * step. Lots that have not been released at time t are excluded rather than
 * counted as zero, which would drag the floor down and invent a spread.
 */
export function envelope(lots, d0, d1, now, metric, n = 160) {
  const out = []
  for (let i = 0; i <= n; i++) {
    const t = d0 + (i / n) * (d1 - d0)
    const vals = []
    for (const l of lots) {
      if (t > now && l.state !== 'done') continue
      const v = valueAt(l.points, t, metric)
      if (v != null) vals.push(v)
    }
    if (!vals.length) continue
    vals.sort((a, b) => a - b)
    out.push({
      t,
      min: vals[0],
      max: vals[vals.length - 1],
      med: vals[Math.floor(vals.length / 2)],
      n: vals.length,
    })
  }
  return out
}

/**
 * Y values at which lots in this cohort were observed waiting on batch
 * partners.
 *
 * The brief asks for the route's declared batch steps. SMT2020 does not put
 * those on the wire, so these are the *observed* ones, taken from segments the
 * simulator attributed to batch waiting (it tracks waiting_time_batching
 * separately, so this is measured rather than inferred). The tradeoff: a batch
 * step no lot has reached yet is not marked.
 */
export function batchBands(lots) {
  const seen = new Set()
  for (const l of lots) {
    for (const p of l.points || []) {
      if (p.reason === 'cohort') seen.add(p.left)
    }
  }
  return [...seen].sort((a, b) => a - b)
}

/** Time domain: earliest release to latest due, so the axis does not move
 *  under the reader while they are looking at it. */
export function domain(lots, now) {
  let t0 = Infinity, t1 = -Infinity
  for (const l of lots) {
    t0 = Math.min(t0, l.release, l.points?.[0]?.t ?? Infinity)
    t1 = Math.max(t1, l.due)
  }
  t1 = Math.max(t1, now)
  if (!isFinite(t0)) t0 = 0
  if (!(t1 > t0)) t1 = t0 + 86400
  const pad = (t1 - t0) * 0.03
  return [t0 - pad, t1 + pad]
}

export function maxValue(lots, metric) {
  let m = 1
  for (const l of lots) {
    if (metric === 'steps') m = Math.max(m, l.route || 0)
    for (const p of l.points || []) m = Math.max(m, metricOf(p, metric))
  }
  return m
}
