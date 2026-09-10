// The dispatch scene's model, kept apart from its rendering.
//
// One dry-etch tool, the lots waiting for its family, the load ports, and the
// overhead transport that carries a chosen lot from where it sits to the port.
// The rail is a directed GRAPH -- nodes and one-way edges -- and everything
// that moves is placed by walking a route on it at a simulated instant. The
// three.js layer (EtchScene.jsx) only draws what this file computes, so the
// story the scene tells ("that vehicle goes the long way round because the
// loop is one-way", "the nearest FOUP was not the one chosen") is arithmetic
// that can be tested, not an animation that has to be eyeballed.
//
// Units are metres and SIMULATED seconds. Nothing here reads a wall clock: a
// job is a pure function of (sim time - its anchor), which is what lets the
// same sequence play at 1x, snap through at 400x, and hold still when the fab
// is paused, exactly like every other view of fab data in the app.
//
// SMT2020 has no AMHS: transport is modelled as Delay steps and a lot starts
// on a tool the instant it is dispatched. The vehicles, ports and shelves here
// are a visualisation of that decision, not a second simulation of it, and
// the scene says so in its legend.

export const V = 4               // vehicle speed along the rail, m/s (real OHTs: 3-5)
export const RAIL_Y = 4.4        // rail height, m
export const CARRY_Y = 2.6        // a FOUP hanging from a vehicle (its top clears the vehicle body)
export const SLOT_Y = 1.35       // a FOUP on a track-side shelf
export const PORT_Y = 0.98       // a FOUP on a load port
export const HOIST_S = 4         // seconds to raise or lower a FOUP
export const DECIDE_S = 6        // the dispatcher's moment on screen, before anything moves
export const PRE_S = 2           // a vehicle waits over the port this long before the lot finishes
export const EXIT_RETURN_S = 10  // off-screen: exit -> back at its parking spot via the interbay

// The loop: two straight rails 2R apart, joined by semicircles. The FRONT
// rail (nearest the viewer) runs +x and carries the stocker shelf and the
// focal tool's load ports; the BACK rail runs -x and carries the under-track
// buffer and two parking spots. One-way, like a real intrabay loop.
export const L = 15, R = 3
export const FRONT_Z = -R, BACK_Z = R
export const TOOL_X = 4.5
export const N_PORTS = 3
export const PORT_PITCH = 1.0
export const SHELF_IN = 0.9      // shelves hang on the aisle side of the rail, this far in
export const ARC_SEGS = 8

export const STOCKER_X = [-13.5, -12, -10.5, -9, -7.5, -6, -4.5, -3]
export const UTS_X = [-6, -8, -10, -12]
// Vehicle parking: one just after the left turn on the front rail (close to
// the stocker), two on the back rail upstream of the buffer.
export const PARK = [
  { id: 'park0', rail: 'front', x: -14.5 },
  { id: 'park1', rail: 'back', x: 0 },
  { id: 'park2', rail: 'back', x: 2 },
]
export const EXIT_X = L + R + 9

export const portNode = i => `lp${i}`
export const stockerNode = i => `s${i}`
export const utsNode = i => `u${i}`

// ---------------------------------------------------------------------------
// Track graph
// ---------------------------------------------------------------------------

export const BATCH_PORTS = 6            // a furnace bay: one port per FOUP of the largest batch
export const BATCH_PORT_PITCH = 0.8

export function buildTrack({ ports = N_PORTS, pitch = PORT_PITCH } = {}) {
  const nodes = new Map()   // id -> {id, x, z}
  const adj = new Map()     // id -> [{to, len}]
  const add = (id, x, z) => {
    nodes.set(id, { id, x, z })
    if (!adj.has(id)) adj.set(id, [])
    return id
  }
  const link = (a, b) => {
    const A = nodes.get(a), B = nodes.get(b)
    adj.get(a).push({ to: b, len: Math.hypot(A.x - B.x, A.z - B.z) })
  }
  const chain = ids => { for (let i = 1; i < ids.length; i++) link(ids[i - 1], ids[i]) }

  // Front rail, +x. Stops sorted by x so the chain is in travel order.
  const front = [{ id: 'fr_start', x: -L }, { id: 'fr_end', x: L }]
  STOCKER_X.forEach((x, i) => front.push({ id: stockerNode(i), x }))
  for (let i = 0; i < ports; i++) front.push({ id: portNode(i), x: TOOL_X + (i - (ports - 1) / 2) * pitch })
  PARK.filter(p => p.rail === 'front').forEach(p => front.push({ id: p.id, x: p.x }))
  front.sort((a, b) => a.x - b.x)
  front.forEach(s => add(s.id, s.x, FRONT_Z))
  chain(front.map(s => s.id))

  // Back rail, -x.
  const back = [{ id: 'br_start', x: L }, { id: 'br_end', x: -L }]
  UTS_X.forEach((x, i) => back.push({ id: utsNode(i), x }))
  PARK.filter(p => p.rail === 'back').forEach(p => back.push({ id: p.id, x: p.x }))
  back.sort((a, b) => b.x - a.x)
  back.forEach(s => add(s.id, s.x, BACK_Z))
  chain(back.map(s => s.id))

  // Right turn: front end -> apex -> back start. Centre (L, 0).
  const right = ['fr_end']
  for (let k = 1; k < ARC_SEGS; k++) {
    const a = -Math.PI / 2 + (Math.PI * k) / ARC_SEGS
    right.push(add(`ra${k}`, L + R * Math.cos(a), R * Math.sin(a)))
  }
  right.push('br_start')
  chain(right)

  // Left turn: back end -> apex -> front start. Centre (-L, 0).
  const left = ['br_end']
  for (let k = 1; k < ARC_SEGS; k++) {
    const a = Math.PI / 2 + (Math.PI * k) / ARC_SEGS
    left.push(add(`la${k}`, -L + R * Math.cos(a), R * Math.sin(a)))
  }
  left.push('fr_start')
  chain(left)

  // Interbay spur off the right apex: where finished lots leave the bay.
  const apex = `ra${ARC_SEGS / 2}`
  add('exit', EXIT_X, 0)
  link(apex, 'exit')

  const cache = new Map()
  const route = (from, to) => {
    const k = `${from}>${to}`
    if (!cache.has(k)) cache.set(k, dijkstra(nodes, adj, from, to))
    return cache.get(k)
  }
  // The closed loop in travel order, for drawing the rail itself.
  const loop = [...front.map(s => s.id), ...right.slice(1, -1), ...back.map(s => s.id), ...left.slice(1, -1)]
    .map(id => nodes.get(id))
  return { nodes, adj, route, loop, apex, ports, pitch }
}

function dijkstra(nodes, adj, from, to) {
  if (!nodes.has(from) || !nodes.has(to)) return null
  const dist = new Map([[from, 0]])
  const prev = new Map()
  const open = new Set([from])
  while (open.size) {
    let u = null, best = Infinity
    for (const n of open) if (dist.get(n) < best) { best = dist.get(n); u = n }
    open.delete(u)
    if (u === to) break
    for (const { to: v, len } of adj.get(u) || []) {
      const nd = best + len
      if (nd < (dist.has(v) ? dist.get(v) : Infinity)) {
        dist.set(v, nd); prev.set(v, u); open.add(v)
      }
    }
  }
  if (!dist.has(to)) return null
  const ids = [to]
  while (ids[0] !== from) ids.unshift(prev.get(ids[0]))
  const pts = ids.map(id => nodes.get(id))
  const cum = [0]
  for (let i = 1; i < pts.length; i++) {
    cum.push(cum[i - 1] + Math.hypot(pts[i].x - pts[i - 1].x, pts[i].z - pts[i - 1].z))
  }
  return { ids, pts, cum, len: dist.get(to) }
}

// Where a vehicle is after travelling `d` metres along a route. Clamped to
// the ends, so an over-run sits at the destination rather than flying off.
export function posAlong(route, d) {
  const { pts, cum, len } = route
  if (pts.length === 1 || d <= 0) return { x: pts[0].x, z: pts[0].z }
  if (d >= len) { const p = pts[pts.length - 1]; return { x: p.x, z: p.z } }
  let i = 1
  while (cum[i] < d) i++
  const a = pts[i - 1], b = pts[i]
  const f = (d - cum[i - 1]) / (cum[i] - cum[i - 1])
  return { x: a.x + (b.x - a.x) * f, z: a.z + (b.z - a.z) * f }
}

// ---------------------------------------------------------------------------
// Where things rest
// ---------------------------------------------------------------------------

export function slotPos(track, id) {
  const n = track.nodes.get(id)
  if (!n) return null
  if (id.startsWith('lp')) return { x: n.x, y: PORT_Y, z: n.z }
  // Shelves hang on the aisle side of their rail.
  const inward = n.z < 0 ? SHELF_IN : -SHELF_IN
  return { x: n.x, y: SLOT_Y, z: n.z + inward }
}

export const carryPos = (track, id) => {
  const n = track.nodes.get(id)
  return { x: n.x, y: CARRY_Y, z: n.z }
}

export const lerp3 = (a, b, f) => ({
  x: a.x + (b.x - a.x) * f, y: a.y + (b.y - a.y) * f, z: a.z + (b.z - a.z) * f,
})

// Stable, cheap, and spreads lot ids across the two shelves. The sim has no
// notion of where a waiting lot physically is, so a hash of its id stands in:
// the same lot always sits in the same place across polls and reloads.
export function hashStr(s) {
  let h = 5381
  for (let i = 0; i < s.length; i++) h = ((h << 5) + h + s.charCodeAt(i)) | 0
  return Math.abs(h)
}

// A shelf slot for a lot that has none. Roughly a third go to the under-track
// buffer on the back rail, the rest to the stocker shelf on the front rail --
// two distinct rail distances to the port, so "nearest" means something.
export function assignSlot(taken, lot) {
  const preferUts = hashStr(lot) % 3 === 0
  const stocker = STOCKER_X.map((_, i) => stockerNode(i))
  const uts = UTS_X.map((_, i) => utsNode(i))
  const order = preferUts ? [...uts, ...stocker] : [...stocker, ...uts]
  // Start within the preferred shelf at a hashed offset so consecutive lots
  // do not all pile into slot 0.
  const pref = preferUts ? uts : stocker
  const off = hashStr(lot + '#') % pref.length
  const rotated = [...pref.slice(off), ...pref.slice(0, off), ...order.filter(s => !pref.includes(s))]
  return rotated.find(s => !taken.has(s)) || null
}

// ---------------------------------------------------------------------------
// Jobs: what a vehicle and a FOUP are doing, as a function of sim time
// ---------------------------------------------------------------------------

// Which vehicle takes a job: the free one with the shortest rail route to
// the pickup. Rail distance, not straight-line -- on a one-way loop the
// vehicle parked 6 m away on the wrong rail is 40 m away in practice. A
// vehicle is free for a job if none of its reservations overlaps the job's
// window; when every vehicle is booked, the one whose clash ends soonest.
// Reservations are intervals rather than a single "busy until", so a pickup
// booked hours ahead (a lot's known finish) does not idle the vehicle now.
export function pickVehicle(track, vehicles, target, at, dur = 60) {
  let best = -1, bestKey = null
  const lt = (a, b) => { for (let i = 0; i < a.length; i++) { if (a[i] !== b[i]) return a[i] < b[i] } return false }
  vehicles.forEach((v, i) => {
    const r = track.route(v.park, target)
    if (!r) return
    const clash = (v.busy || []).filter(b => b.from < at + dur && b.to > at)
    const key = clash.length ? [1, Math.max(...clash.map(b => b.to)), r.len] : [0, r.len, 0]
    if (bestKey === null || lt(key, bestKey)) { best = i; bestKey = key }
  })
  return best
}

// The soonest a vehicle can take a job wanted at `at`: the pick at `at` if
// one is free, else the moment the first clash ends, re-checked. A batch of
// FOUPs asks for several vehicles at once; this is what spaces them out.
export function earliestFree(track, vehicles, target, at, dur = 60) {
  let t = at
  for (let k = 0; k < 6; k++) {
    const i = pickVehicle(track, vehicles, target, t, dur)
    if (i < 0) return { vehicle: -1, at: t }
    const clash = (vehicles[i].busy || []).filter(b => b.from < t + dur && b.to > t)
    if (!clash.length) return { vehicle: i, at: t }
    t = Math.max(...clash.map(b => b.to)) + 0.5
  }
  return { vehicle: pickVehicle(track, vehicles, target, t, dur), at: t }
}

export function reserveVehicle(v, from, to) {
  (v.busy || (v.busy = [])).push({ from, to })
}

export function pruneReservations(vehicles, before) {
  for (const v of vehicles) if (v.busy) v.busy = v.busy.filter(b => b.to >= before)
}

// A lot arriving on a port. Phases, from the decision at t0:
//   select   [0, d1)   the dispatcher's choice is made and shown
//   reserve  [d1, d3)  the vehicle drives to the FOUP and hoists it
//   transit  [d3, d4)  FOUP under the vehicle, on its way to the port
//   load     [d4, d5)  FOUP lowered onto the port
//   process  [d5, ...) on the tool; the vehicle returns to its spot until d6
// `changeover` is the setup time the tool pays before processing this lot
// (a litho track switching layers, an implanter switching species): the
// FOUP is on the port and the tool is busy, but not yet with wafers.
export function planArrival(track, { lot, t0, slot, port, vehicle, park, delay = 0, changeover = 0 }) {
  const lp = portNode(port)
  const r1 = track.route(park, slot), r2 = track.route(slot, lp), r3 = track.route(lp, park)
  const d1 = DECIDE_S + delay
  const d2 = d1 + r1.len / V
  const d3 = d2 + HOIST_S
  const d4 = d3 + r2.len / V
  const d5 = d4 + HOIST_S
  const d6 = d5 + r3.len / V
  const d7 = d5 + Math.max(0, changeover || 0)
  return { kind: 'arrival', lot, t0, slot, port, lp, vehicle, park, delay, changeover: Math.max(0, changeover || 0), r1, r2, r3, d1, d2, d3, d4, d5, d6, d7 }
}

export function arrivalPhase(job, tau) {
  if (tau < job.d1) return 'select'
  if (tau < job.d3) return 'reserve'
  if (tau < job.d4) return 'transit'
  if (tau < job.d5) return 'load'
  if (job.d7 != null && tau < job.d7) return 'changeover'
  return 'process'
}

// Positions at tau seconds after the decision. `vehicle` is null when the
// vehicle has nothing to do with this job any more (it is back at its spot).
export function arrivalAt(track, job, tau) {
  const slot = slotPos(track, job.slot)
  const port = slotPos(track, job.lp)
  const phase = arrivalPhase(job, tau)
  if (tau < job.d1) {
    return { phase, foup: slot, vehicle: null, carrying: false, route: null }
  }
  if (tau < job.d2) {
    const p = posAlong(job.r1, (tau - job.d1) * V)
    return { phase, foup: slot, vehicle: { x: p.x, z: p.z }, carrying: false, route: job.r1 }
  }
  if (tau < job.d3) {
    const f = (tau - job.d2) / HOIST_S
    const n = track.nodes.get(job.slot)
    return { phase, foup: lerp3(slot, carryPos(track, job.slot), f), vehicle: { x: n.x, z: n.z },
             carrying: true, hoist: f, route: job.r2 }
  }
  if (tau < job.d4) {
    const p = posAlong(job.r2, (tau - job.d3) * V)
    return { phase, foup: { x: p.x, y: CARRY_Y, z: p.z }, vehicle: p, carrying: true, hoist: 1, route: job.r2 }
  }
  if (tau < job.d5) {
    const f = (tau - job.d4) / HOIST_S
    const n = track.nodes.get(job.lp)
    return { phase, foup: lerp3(carryPos(track, job.lp), port, f), vehicle: { x: n.x, z: n.z },
             carrying: true, hoist: 1 - f, route: null }
  }
  if (tau < job.d6) {
    const p = posAlong(job.r3, (tau - job.d5) * V)
    return { phase, foup: port, vehicle: p, carrying: false, route: null }
  }
  return { phase, foup: port, vehicle: null, carrying: false, route: null }
}

// Share of a changeover done, 0..1, or null when the job has none / is past it.
export function changeoverProgress(job, tau) {
  if (!job || !job.changeover || tau == null || tau < job.d5 || tau >= job.d7) return null
  return (tau - job.d5) / job.changeover
}

// A finished lot leaving a port. Anchored at tEnd, the lot's finish:
//   approach [e0, e1)  the vehicle drives over from its spot
//   wait     [e1, 0)   it waits above the port for the lot to finish
//   unload   [0, e3)   FOUP hoisted off the port
//   exit     [e3, e4)  carried out of the bay via the interbay spur
//   gone     [e4, e5)  off screen; back at its spot at e5
// `delay` shifts the pickup past the finish: a batch of FOUPs finishes as one
// but leaves one vehicle at a time.
export function planDeparture(track, { lot, tEnd, port, vehicle, park, delay = 0 }) {
  const lp = portNode(port)
  const r1 = track.route(park, lp), r2 = track.route(lp, 'exit')
  const e2 = delay
  const e1 = e2 - PRE_S
  const e0 = e1 - r1.len / V
  const e3 = e2 + HOIST_S
  const e4 = e3 + r2.len / V
  const e5 = e4 + EXIT_RETURN_S
  return { kind: 'departure', lot, tEnd, port, lp, vehicle, park, delay, r1, r2, e0, e1, e2, e3, e4, e5 }
}

export function departurePhase(job, tau) {
  if (tau < job.e0) return 'idle'
  if (tau < job.e1) return 'approach'
  if (tau < job.e2) return 'wait'
  if (tau < job.e3) return 'unload'
  if (tau < job.e4) return 'exit'
  if (tau < job.e5) return 'gone'
  return 'done'
}

export function departureAt(track, job, tau) {
  const port = slotPos(track, job.lp)
  const n = track.nodes.get(job.lp)
  const phase = departurePhase(job, tau)
  if (phase === 'idle' || phase === 'done') return { phase, foup: phase === 'idle' ? port : null, vehicle: null, carrying: false, route: null }
  if (phase === 'approach') {
    const p = posAlong(job.r1, (tau - job.e0) * V)
    return { phase, foup: port, vehicle: p, carrying: false, route: job.r1 }
  }
  if (phase === 'wait') return { phase, foup: port, vehicle: { x: n.x, z: n.z }, carrying: false, route: null }
  if (phase === 'unload') {
    const f = (tau - job.e2) / HOIST_S
    return { phase, foup: lerp3(port, carryPos(track, job.lp), f), vehicle: { x: n.x, z: n.z },
             carrying: true, hoist: 1 - f, route: job.r2 }
  }
  if (phase === 'exit') {
    const p = posAlong(job.r2, (tau - job.e3) * V)
    return { phase, foup: { x: p.x, y: CARRY_Y, z: p.z }, vehicle: p, carrying: true, hoist: 0, route: job.r2 }
  }
  return { phase, foup: null, vehicle: null, carrying: false, route: null }   // gone
}

// ---------------------------------------------------------------------------
// The tool's state, for the strip under the scene
// ---------------------------------------------------------------------------

export const TOOL_STATES = ['IDLE', 'LOT SELECTED', 'RESERVED', 'FOUP IN TRANSIT', 'LOADING', 'PROCESSING']
// A furnace has one more state, and it is the interesting one: idle on
// purpose, holding for enough compatible lots to fill a batch.
export const FURNACE_STATES = ['IDLE', 'WAITING FOR BATCH', 'BATCH SELECTED', 'RESERVED', 'FOUP IN TRANSIT', 'LOADING', 'PROCESSING']
// A litho track pays a changeover when the layer's setup differs from the one
// it is on, so that is a state of its own between loading and processing.
export const LITHO_STATES = ['IDLE', 'LOT SELECTED', 'RESERVED', 'FOUP IN TRANSIT', 'LOADING', 'SETUP CHANGE', 'PROCESSING']
export const statesFor = kind => kind === 'furnace' ? FURNACE_STATES : kind === 'litho' ? LITHO_STATES : TOOL_STATES

// A stable hue per setup name, for lids and the tool's setup band.
export function setupHue(setup) {
  if (!setup || setup === '-') return null
  return hashStr(String(setup)) % 360
}

// The changeover a decision recorded for a chosen lot: the `setup_s` slot of
// its ranking tuple, when the rule's keys name one. The simulator charges
// setup time only when the tool's setup actually changes, and the ranking
// prices it the same way, so a positive value here IS a changeover. (The
// record's `tool_setup` and `setup_match` are stamped after the switch, so
// for the chosen lot they read as matching even when it changed the tool.)
// Seconds; 0 without a change or a priced slot.
export function setupCostOf(decision, lot) {
  const w = decision && decision.why
  if (!w) return 0
  const i = (w.keys || []).indexOf('setup_s')
  const c = [...(w.chosen || [])].find(x => x.lot === lot)
  if (!c || i < 0 || !Array.isArray(c.tuple)) return 0
  const v = Number(c.tuple[i])
  return Number.isFinite(v) && v > 0 ? v : 0
}

// Compatible groups in a furnace queue: SMT2020 batches lots of the same
// product at the same step. Returns groups largest first, each with the
// step's min/max batch (in lots) when the records carry them.
export function batchGroups(waiting, steps) {
  const groups = new Map()
  for (const lot of waiting || []) {
    const st = (steps || {})[lot]
    if (!st || !st.step) continue
    const part = st.part || partOf(lot)
    const key = `${part}|${st.step}`
    if (!groups.has(key)) groups.set(key, { part, step: st.step, lots: [], bmin: st.bmin ?? null, bmax: st.bmax ?? null })
    groups.get(key).lots.push(lot)
  }
  return [...groups.values()].sort((a, b) => b.lots.length - a.lots.length || a.step.localeCompare(b.step))
}

// "Lot_10_9431" -> "part_10": the product is in the lot id.
export function partOf(lot) {
  const m = String(lot || '').match(/^\w+?_(\d+)_\d+$/)
  return m ? `part_${m[1]}` : null
}

const PHASE_STATE = {
  select: 'LOT SELECTED', reserve: 'RESERVED', transit: 'FOUP IN TRANSIT', load: 'LOADING', changeover: 'SETUP CHANGE', process: 'PROCESSING',
}

// The newest arrival decides the headline state; PROCESSING if anything is on
// the tool and nothing newer is in flight; IDLE when nothing is.
export function toolState({ newest, tau, processing, online, kind, gathering }) {
  if (online === false) return 'DOWN'
  if (newest && tau != null) {
    const ph = arrivalPhase(newest, tau)
    if (ph !== 'process') return ph === 'select' && kind === 'furnace' ? 'BATCH SELECTED' : PHASE_STATE[ph]
  }
  if (processing > 0) return 'PROCESSING'
  // A furnace with lots queued and nothing running is holding for a batch.
  if (kind === 'furnace' && gathering) return 'WAITING FOR BATCH'
  return 'IDLE'
}

// Fraction through the decision window, 0..1, or null outside it. Drives the
// panel's scan-and-lock animation.
export function decideProgress(job, tau) {
  if (!job || tau == null || tau < 0 || tau >= DECIDE_S) return null
  return tau / DECIDE_S
}

// Rail distance from a resting place to a port, for the panel's "rail m"
// column: the number that makes "nearest, but not chosen" visible.
export function railDistance(track, slot, port) {
  if (!slot) return null
  const r = track.route(slot, portNode(port))
  return r ? r.len : null
}

export const fmtDur = (s) => {
  if (s == null) return '—'
  const a = Math.abs(s), sign = s < 0 ? '-' : ''
  if (a < 3600) return `${sign}${Math.round(a / 60)} min`
  if (a < 86400) return `${sign}${(a / 3600).toFixed(1)} h`
  return `${sign}${(a / 86400).toFixed(1)} d`
}

// Which tool families get a scene, and which tool model: dry etch (SMT2020's
// DE_FE_* / DE_BE_*) as a cluster etch tool, planarisation (Planar_*) as a
// CMP polisher. The bay, the track and the dispatch machinery are shared; the
// focal tool and what the panel emphasises differ.
export const sceneKind = g => /^DE_/i.test(String(g || '')) ? 'etch' : /^Planar_/i.test(String(g || '')) ? 'cmp'
  : /^Diffusion_/i.test(String(g || '')) ? 'furnace' : /^Litho(Track)?_(FE|BE)_/i.test(String(g || '')) ? 'litho' : null
export const isSceneFamily = g => sceneKind(g) != null
export const isEtchFamily = g => sceneKind(g) === 'etch'
export const KIND_LABEL = { etch: 'plasma etch cluster tool · 4 chambers', cmp: 'CMP polisher · 3 platens + post-clean',
  furnace: 'vertical diffusion furnace · 2 tubes · batches of 3-6 lots',
  litho: 'litho cell · coat/develop track + scanner · one setup per layer' }

// ---------------------------------------------------------------------------
// Replaying a recorded decision
// ---------------------------------------------------------------------------

export const REPLAY_LEAD_S = 10   // the replay clock starts this long before the decision

// The two tool snapshots a replay needs, built from one recorded decision:
// `before` -- the previous lot still on a port and the candidates on the
// shelves -- and `after`, at the decision instant t0: the previous lot gone
// and the chosen lot(s) started. Feeding the scene `before`, then `after`
// once its clock passes t0, replays the departure and the dispatch with the
// same machinery that draws the live feed. The chosen lots' finish is not in
// the record, so they carry no countdown. `prevLot` (the lot the tool freed
// from, usually the previous decision's winner) is optional; without it the
// replay starts from an idle tool.
export function replayScenario({ tool, decision, prevLot, t0 }) {
  const why = decision.why || {}
  const chosen = (why.chosen || []).map(c => c.lot)
  const alts = (why.alternatives || []).map(a => a.lot).filter(l => !chosen.includes(l))
  const waiting = [...chosen, ...alts]
  const qbefore = Number(decision.qbefore)
  const runningBefore = prevLot ? [{ lot: prevLot, t: t0 - 3600, end: t0 }] : []
  const base = {
    ...tool,
    setup: why.tool_setup ?? tool.setup,
    recent_decisions: [decision],
    recent_events: [],
    cohorts: {},
    sim_t: null, sim_t_at: null, speed: 1, paused: false,
  }
  const before = {
    ...base,
    running: runningBefore.map(r => r.lot), running_count: runningBefore.length, running_lots: runningBefore,
    waiting, waiting_count: Math.max(waiting.length, Number.isFinite(qbefore) ? qbefore : 0),
    recent_out: [],
  }
  const after = {
    ...base,
    running: chosen, running_count: chosen.length,
    running_lots: chosen.map(l => ({ lot: l, t: t0, end: null })),
    waiting: alts, waiting_count: Math.max(alts.length, before.waiting_count - chosen.length),
    recent_out: prevLot ? [{ lot: prevLot, day: t0 / 86400 }] : [],
  }
  return { before, after, t0, start: t0 - REPLAY_LEAD_S }
}

// The lot a tool freed from, for a replay: the previous decision's winner.
export function prevLotOf(decisions, index) {
  const p = decisions[index + 1]
  const c = p && p.why && p.why.chosen && p.why.chosen[0]
  return c ? c.lot : null
}

// The newest recorded decision on a tool that dispatched `lot` there: what a
// journey's "replay on this tool" link asks for. Null when the tool's recent
// decisions do not name it (older than the window kept, or no rationale).
export function decisionForLot(decisions, lot) {
  for (const d of decisions || []) {
    const chosen = d && d.why && d.why.chosen
    if (chosen && chosen.some(c => c.lot === lot)) return d
  }
  return null
}

// The setup a tool was on before a decision: the previous decision's
// stamped setup (each record carries the tool's setup after its own switch).
export function setupBefore(decisions, index) {
  const p = (decisions || [])[index + 1]
  if (!p) return null
  const su = p.setup && p.setup !== '-' ? p.setup : (p.why && p.why.tool_setup)
  return su && su !== '-' ? su : null
}
