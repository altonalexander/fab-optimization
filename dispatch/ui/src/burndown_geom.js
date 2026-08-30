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
 * Warm-up history followed by the live stream, as one series.
 *
 * The two arrive separately — history on the compacted state topic, live points
 * on the burndown stream — and are kept apart in the payload so the chart can
 * draw them differently. Anything that reasons about a lot's *shape* (the
 * envelope, the y-domain, a value lookup) wants them joined, or an active lot
 * looks like it sprang into existence at the warm-up line with no past.
 *
 * History carries only (t, left); `rem_s` is absent, so a process-time series
 * legitimately has no historic half.
 */
export function allPoints(lot) {
  const h = lot.history || []
  const p = lot.points || []
  if (!h.length) return p
  if (!p.length) return h
  // The snapshot is taken between two steps, so the join is contiguous rather
  // than overlapping; drop anything at or before the last history point only
  // if the feed ever republishes across the line.
  const cut = h[h.length - 1].t
  return h.concat(p.filter(x => x.t > cut))
}

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
  // A scrapped lot's line just ends. It is not waiting for anything.
  if (lot.state !== 'done' && lot.state !== 'scrapped' && now > last.t) {
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
      const v = valueAt(allPoints(l), t, metric)
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
    // Start at the earliest thing we can actually draw. With warm-up history
    // that is the first historic point, not the first live one.
    const first = allPoints(l)[0]?.t ?? Infinity
    t0 = Math.min(t0, l.release, first)
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
    for (const p of allPoints(l)) m = Math.max(m, metricOf(p, metric) || 0)
  }
  return m
}


/**
 * The gray projected ray: where this lot lands if it keeps going at the rate
 * its product and lot type have actually shown.
 *
 * Returns null when there is nothing honest to draw — a finished lot, a
 * scrapped one, or a lot the server could not fit a rate for. A scrapped lot
 * gets no ray on purpose: it is not going to complete, and drawing a dotted
 * line to a completion date would assert the opposite.
 *
 * For the process-time metric the ray runs from the lot's remaining process
 * time to zero over the same interval, so both metrics agree on *when* rather
 * than disagreeing about the shape of the descent.
 */
export function projection(lot, metric, now) {
  const p = lot.projection
  if (!p || lot.state === 'done' || lot.state === 'scrapped') return null
  // Joined, not `lot.points`: a lot that has not moved since the warm-up
  // snapshot has history and no live points at all, and the API keeps those
  // deliberately -- they are the stalled lots worth looking at. Reading only
  // the live half denied a ray to exactly those lots, which is backwards: the
  // one that has not moved in days is the one whose projected finish matters.
  const pts = allPoints(lot)
  if (!pts.length) return null
  const last = pts[pts.length - 1]
  const v0 = metricOf(last, metric)
  // History carries only (t, left). A process-time ray for a history-only lot
  // therefore has no value to start from, and is correctly absent rather than
  // guessed at.
  if (!v0) return null
  const t0 = Math.max(p.start_t ?? last.t, now ?? last.t)
  return { t1: t0, v1: v0, t2: p.eta_t, v2: 0, eta: p.eta_t, basis: p.basis }
}

/**
 * Points where the burndown went back up.
 *
 * This is rework: steps the lot had already completed are put back in front of
 * it, so it has more steps *remaining* while the total route length is
 * unchanged. The lot has gone back in the line and must redo them. Marking the
 * jogs makes that readable as an event rather than as a data glitch.
 */
export function reworkJogs(lot) {
  const pts = lot.points || []
  const out = []
  for (let i = 1; i < pts.length; i++) {
    if (pts[i].left > pts[i - 1].left) {
      out.push({ t: pts[i].t, from: pts[i - 1].left, to: pts[i].left,
                 steps: pts[i].left - pts[i - 1].left })
    }
  }
  return out
}

/** Clear air to the right of the last thing worth reading: the latest due date
 *  or projected completion, whichever is further out. A due marker sitting on
 *  the frame cannot be read as early or late, and a projection ray that ends at
 *  the edge looks truncated rather than finished. Five days is roughly a week
 *  of fab time at LVHM release rates -- enough to see where a ray lands
 *  relative to its due rule without shrinking the live part of the chart. */
export const RIGHT_PAD_S = 5 * 86400

/** Domain must cover projected completions and due dates too, or the ray and
 *  the due-date rule get clipped off the right edge. */
export function domainWithProjection(lots, now, metric) {
  let [t0, t1] = domain(lots, now)
  let last = -Infinity
  for (const l of lots) {
    if (l.due) last = Math.max(last, l.due)
    const pr = projection(l, metric, now)
    if (pr) last = Math.max(last, pr.t2)
  }
  if (isFinite(last)) t1 = Math.max(t1, last + RIGHT_PAD_S)
  // The proportional pad still applies when there is nothing to anchor to --
  // a cohort with no due dates and no projectable lots.
  return [t0, Math.max(t1, t1 + (t1 - t0) * 0.02)]
}


/**
 * Step-after segments for the warm-up half of a lot's life.
 *
 * Drawn separately from the live half so the chart can colour them
 * differently: everything here happened before the simulation the user is
 * watching started, and reading a warm-up stall as something the current run
 * caused would be the wrong conclusion.
 *
 * The trailing run joins the last historic point to the first live one. That
 * wait began during warm-up, so it belongs to history even though it ends
 * after the line.
 */
export function historySegments(lot, metric) {
  if (metric !== 'steps') return []      // history carries no rem_s
  const h = lot.history || []
  if (h.length < 2) return []
  const out = []
  for (let i = 1; i < h.length; i++) {
    const a = h[i - 1], b = h[i]
    out.push({ t1: a.t, v1: a.left, t2: b.t, v2: a.left, flat: true, historic: true })
    out.push({ t1: b.t, v1: a.left, t2: b.t, v2: b.left, flat: false, historic: true })
  }
  const last = h[h.length - 1]
  const firstLive = (lot.points || [])[0]
  if (firstLive && firstLive.t > last.t) {
    out.push({ t1: last.t, v1: last.left, t2: firstLive.t, v2: last.left,
               flat: true, historic: true })
    out.push({ t1: firstLive.t, v1: last.left, t2: firstLive.t,
               v2: firstLive.left, flat: false, historic: true })
  }
  return out
}
