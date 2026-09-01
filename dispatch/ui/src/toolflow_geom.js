// Layout and arithmetic for the tool page's flow strip: queue -> tool -> out.
// Pure, so the choices ("the ninth cell says +n", "a batch is ringed") are
// tested rather than eyeballed.

export const GRID = 9          // 3x3 queue cells
export const OUT_SHOWN = 3     // recent departures listed by id
export const RING_MAX = 8      // lots drawn inside the box before "+n"

// The 3x3 queue. With more lots than cells the last cell becomes "+n" and
// covers everything not drawn, so the grid always accounts for the whole
// queue. `waiting` may be a prefix of the queue (the API caps it), which is
// why the count travels separately.
export function queueCells(waiting, total, n = GRID) {
  const ids = waiting || []
  const count = Math.max(total ?? ids.length, ids.length)
  const cells = []
  if (count <= n) {
    for (let i = 0; i < n; i++) {
      cells.push(i < ids.length ? { kind: 'lot', id: ids[i] } : { kind: 'empty' })
    }
    return cells
  }
  for (let i = 0; i < n - 1; i++) {
    cells.push(i < ids.length ? { kind: 'lot', id: ids[i] } : { kind: 'empty' })
  }
  cells.push({ kind: 'more', n: count - (n - 1) })
  return cells
}

// Where k lots sit inside a square of side `size`: one lot in the middle, a
// batch on a ring. Returns fractions of the box (0..1) so CSS does the rest.
export function ringLayout(k, max = RING_MAX) {
  const shown = Math.min(k, max)
  if (shown <= 0) return { points: [], extra: 0 }
  if (shown === 1) return { points: [{ x: 0.5, y: 0.5 }], extra: k - 1 }
  // The ring widens as it fills so neighbours stay clear of each other, and
  // sits a touch below centre so the twelve o'clock lot clears the setup tag
  // straddling the top edge. Sized for a 180px box and 50px lots.
  const r = shown <= 4 ? 0.28 : shown <= 6 ? 0.34 : 0.36
  const cy = 0.53
  const points = []
  for (let i = 0; i < shown; i++) {
    const a = -Math.PI / 2 + (2 * Math.PI * i) / shown
    points.push({ x: 0.5 + r * Math.cos(a), y: cy + r * Math.sin(a) })
  }
  return { points, extra: k - shown }
}

// The last few departures by id, and how many more the ring remembers.
export function recentOut(list, max = OUT_SHOWN) {
  const rows = list || []
  return { shown: rows.slice(0, max), extra: Math.max(0, rows.length - max) }
}

// "Lot_3_1234" -> { short: "1234", hot: false }; "HotLot_4_88" -> hot.
// The stream name carries no information a circle has room for; the index
// is what tells two lots apart, and hot is what the eye should catch.
export function shortId(id) {
  const s = String(id ?? '')
  const hot = /^hot/i.test(s)
  const m = s.match(/(\d+)$/)
  return { short: m ? m[1] : s, hot }
}

// The simulated clock right now, extrapolated from the last reading at the
// playback speed. Held while paused; never runs backwards past the reading.
export function simNow(clock, wallNowS) {
  if (!clock || clock.t == null) return null
  if (clock.paused || !clock.speed || clock.t_at == null) return clock.t
  return clock.t + Math.max(0, wallNowS - clock.t_at) * clock.speed
}

// Seconds of process time left on a run, or null when the start carried no
// finish time. Clamped at zero: a lot the feed has not yet reported off the
// tool reads "done", not negative.
export function remaining(meta, clock, wallNowS) {
  if (!meta || meta.end == null) return null
  const now = simNow(clock, wallNowS)
  if (now == null) return null
  return Math.max(0, meta.end - now)
}

// Share of the run elapsed, 0..1, for the progress ring. Null without both
// ends of the run.
export function progress(meta, clock, wallNowS) {
  if (!meta || meta.end == null || meta.t == null || meta.end <= meta.t) return null
  const now = simNow(clock, wallNowS)
  if (now == null) return null
  return Math.min(1, Math.max(0, (now - meta.t) / (meta.end - meta.t)))
}

// Countdown text. Two units at most, the unit shrinking as time runs out,
// which is what a countdown reads like: "2h 05m", "4m 30s", "12s".
export function fmtCountdown(s) {
  if (s == null) return '—'
  const v = Math.max(0, Math.round(s))
  const h = Math.floor(v / 3600), m = Math.floor((v % 3600) / 60), sec = v % 60
  const pad = n => String(n).padStart(2, '0')
  if (h > 0) return `${h}h ${pad(m)}m`
  if (m > 0) return `${m}m ${pad(sec)}s`
  return `${sec}s`
}

// A setup label the feed uses "-" for "none" on; treat that and blanks alike.
export function setupLabel(setup) {
  const s = (setup ?? '').toString().trim()
  return s && s !== '-' ? s : null
}
