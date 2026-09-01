// Layout for a lot's journey strip: the two steps behind it, the one it is at,
// the two ahead, drawn as boxes whose width is the step's nominal process
// time. Pure, so the width rule is tested rather than eyeballed.

export const MIN_SHARE = 0.09   // a two-second metrology step still gets a box

// Widths as shares of the strip (they sum to 1). Proportional to process time,
// with a floor so every step stays legible. Zero or unknown
// times share the floor.
export function widths(steps) {
  const n = steps.length
  if (!n) return []
  const t = steps.map(s => Math.max(0, Number(s.proc_s) || 0))
  const sum = t.reduce((a, b) => a + b, 0)
  // Every box gets the floor; what is left of the strip is split by time.
  // Flooring after normalising would let the renormalisation eat the floor.
  const free = 1 - n * MIN_SHARE
  return t.map(v => MIN_SHARE + free * (sum > 0 ? v / sum : 1 / n))
}

// The step's family as a short label: "Litho_FE_115" -> "Litho FE 115".
export function famLabel(fam) {
  return String(fam || '?').replace(/_/g, ' ')
}

// "2h 05m" / "45m" / "30s" for a nominal duration; different from a countdown
// in that it does not need seconds precision.
export function fmtProc(s) {
  const v = Math.max(0, Math.round(Number(s) || 0))
  if (v >= 3600) return `${Math.floor(v / 3600)}h ${String(Math.floor((v % 3600) / 60)).padStart(2, '0')}m`
  if (v >= 60) return `${Math.floor(v / 60)}m`
  return `${v}s`
}

// Delay_* is the simulator's pseudo-toolset for a route-prescribed wait (ADR
// 0008): the lot is holding, not being processed, and there is no tool to see.
export const isDelay = fam => /^Delay(_|$)/i.test(String(fam || ''))

// What to say about the lot's position: on a tool, holding, waiting, or done.
export function statusOf(j) {
  if (!j) return 'unknown'
  if (j.idx >= j.n) return 'done'
  if (j.tool && isDelay(j.tool)) return 'holding (route delay)'
  if (j.tool) return 'on tool'
  if (j.waiting) return 'waiting'
  return 'in transit'
}
