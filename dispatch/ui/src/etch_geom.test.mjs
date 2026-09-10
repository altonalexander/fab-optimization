// node --test src/etch_geom.test.mjs
import { test } from 'node:test'
import assert from 'node:assert/strict'
import {
  buildTrack, posAlong, slotPos, assignSlot, pickVehicle, reserveVehicle, pruneReservations, replayScenario, prevLotOf, REPLAY_LEAD_S, decisionForLot, sceneKind, isSceneFamily, earliestFree, batchGroups, partOf, statesFor, BATCH_PORTS, BATCH_PORT_PITCH, changeoverProgress, setupHue, setupCostOf, setupBefore, planArrival, arrivalAt, arrivalPhase,
  planDeparture, departureAt, toolState, decideProgress, railDistance,
  V, L, R, HOIST_S, DECIDE_S, PRE_S, STOCKER_X, UTS_X, PARK, TOOL_X, N_PORTS, PORT_Y, SLOT_Y, CARRY_Y,
  portNode, stockerNode, utsNode,
} from './etch_geom.js'

const track = buildTrack()
const near = (a, b, eps = 1e-6) => assert.ok(Math.abs(a - b) < eps, `${a} != ${b}`)

test('every stop is reachable from every other stop (the loop is closed)', () => {
  const stops = [...STOCKER_X.map((_, i) => stockerNode(i)), ...UTS_X.map((_, i) => utsNode(i)),
                 ...Array.from({ length: N_PORTS }, (_, i) => portNode(i)), ...PARK.map(p => p.id)]
  for (const a of stops) for (const b of stops) {
    if (a === b) continue
    const r = track.route(a, b)
    assert.ok(r && r.len > 0, `${a} -> ${b}`)
    assert.equal(r.ids[0], a); assert.equal(r.ids[r.ids.length - 1], b)
  }
})

test('along one rail the route is the straight distance; against it, the long way round', () => {
  // stocker slot 0 (x=-13.5) to the middle port (x=TOOL_X), both on the front rail
  const r = track.route(stockerNode(0), portNode(1))
  near(r.len, TOOL_X - STOCKER_X[0])
  // under-track buffer (back rail, runs -x) to the port: back to the left turn and round
  const u = track.route(utsNode(0), portNode(1))
  const expect = (UTS_X[0] + L) + Math.PI * R * chordFactor() + (TOOL_X + L)
  near(u.len, expect, 1e-6)
  assert.ok(u.len > 2 * r.len, 'the buffer is the far shelf')
})

// The arcs are polylines, so their length is a little under pi*R.
function chordFactor() {
  const n = 8
  return (2 * n * Math.sin(Math.PI / (2 * n))) / Math.PI
}

test('exit is only reachable through the right apex', () => {
  const r = track.route(portNode(2), 'exit')
  assert.ok(r.ids.includes(track.apex))
  assert.equal(track.route('exit', portNode(0)), null, 'the spur is a dead end')
})

test('posAlong walks the route and clamps at both ends', () => {
  const r = track.route(stockerNode(0), portNode(1))
  const p0 = posAlong(r, -5), p1 = posAlong(r, r.len + 5), pm = posAlong(r, r.len / 2)
  near(p0.x, STOCKER_X[0]); near(p1.x, TOOL_X)
  near(pm.x, (STOCKER_X[0] + TOOL_X) / 2)
  const u = track.route(utsNode(0), portNode(1))
  // past the turn, the vehicle is on the front rail
  const q = posAlong(u, u.len - 1)
  near(q.z, -R)
})

test('shelf slots hang on the aisle side; ports sit under the front rail', () => {
  const s = slotPos(track, stockerNode(0)), u = slotPos(track, utsNode(0)), p = slotPos(track, portNode(0))
  assert.ok(s.z > -R && s.z < 0); assert.ok(u.z < R && u.z > 0)
  near(p.z, -R); near(p.y, PORT_Y); near(s.y, SLOT_Y)
})

test('slot assignment is stable per lot and never double-books', () => {
  const taken = new Set()
  const lots = Array.from({ length: 12 }, (_, i) => `Lot_6_${5400 + i}`)
  const got = lots.map(l => { const s = assignSlot(taken, l); taken.add(s); return s })
  assert.equal(new Set(got).size, 12)
  assert.equal(assignSlot(taken, 'Lot_9_1'), null, 'a 13th lot has nowhere to sit')
  assert.equal(assignSlot(new Set(), 'Lot_6_5459'), assignSlot(new Set(), 'Lot_6_5459'))
  assert.ok(got.some(s => s.startsWith('u')) && got.some(s => s.startsWith('s')), 'both shelves get used')
})

test('the nearest free vehicle by rail takes the job, even if another is nearer as the crow flies', () => {
  const vehicles = PARK.map(p => ({ park: p.id, busy: [] }))
  // Stocker pickup: park0 sits just before the shelf on the same rail.
  assert.equal(pickVehicle(track, vehicles, stockerNode(0), 100), 0)
  // Buffer pickup: park1/2 are upstream on the back rail; park0 would loop.
  assert.equal(pickVehicle(track, vehicles, utsNode(0), 100), 1)
  // park1 booked over the window -> park2, the next on that rail, not park0 across the aisle.
  reserveVehicle(vehicles[1], 90, 500)
  assert.equal(pickVehicle(track, vehicles, utsNode(0), 100), 2)
  // A booking hours ahead does not idle the vehicle now.
  reserveVehicle(vehicles[2], 5000, 5100)
  assert.equal(pickVehicle(track, vehicles, utsNode(0), 100), 2)
  // Everyone clashing -> whoever frees first.
  reserveVehicle(vehicles[0], 50, 300); reserveVehicle(vehicles[2], 50, 400)
  assert.equal(pickVehicle(track, vehicles, utsNode(0), 100), 0)
  pruneReservations(vehicles, 450)
  assert.deepEqual(vehicles[0].busy, []); assert.equal(vehicles[1].busy.length, 1)
})

test('a recorded decision becomes a before/after pair the scene can replay', () => {
  const decision = { day: '90.148', qbefore: '18', src: 'rule:fifo', why: {
    rule: 'rule:fifo', tool_setup: 'SU1', chosen: [{ lot: 'Lot_3_2439' }],
    alternatives: [{ lot: 'Lot_6_5508' }, { lot: 'Lot_4_3482' }, { lot: 'Lot_3_2439' }] } }
  const tool = { id: 'DE_FE_86_204', group: 'DE_FE_86', online: true, setup: '-', running_lots: [{ lot: 'X', t: 1, end: 2 }] }
  const sc = replayScenario({ tool, decision, prevLot: 'Lot_5_4474', t0: 1000 })
  assert.equal(sc.start, 1000 - REPLAY_LEAD_S)
  assert.deepEqual(sc.before.running, ['Lot_5_4474'])
  assert.equal(sc.before.running_lots[0].end, 1000)
  assert.deepEqual(sc.before.waiting, ['Lot_3_2439', 'Lot_6_5508', 'Lot_4_3482'], 'winner first, no duplicates')
  assert.equal(sc.before.waiting_count, 18)
  assert.deepEqual(sc.after.running, ['Lot_3_2439'])
  assert.equal(sc.after.running_lots[0].t, 1000); assert.equal(sc.after.running_lots[0].end, null, 'no invented finish')
  assert.deepEqual(sc.after.waiting, ['Lot_6_5508', 'Lot_4_3482'])
  assert.equal(sc.after.recent_out[0].lot, 'Lot_5_4474')
  assert.equal(sc.after.setup, 'SU1')
  const idle = replayScenario({ tool, decision, prevLot: null, t0: 1000 })
  assert.deepEqual(idle.before.running, []); assert.deepEqual(idle.after.recent_out, [])
  assert.equal(prevLotOf([decision, decision], 0), 'Lot_3_2439')
  assert.equal(prevLotOf([decision], 0), null)
})

test('a furnace bay has six ports on a tighter pitch, and the rest of the loop is unchanged', () => {
  const six = buildTrack({ ports: BATCH_PORTS, pitch: BATCH_PORT_PITCH })
  assert.equal(six.ports, 6)
  for (let i = 0; i < 6; i++) assert.ok(six.nodes.has(portNode(i)))
  assert.ok(!six.nodes.has(portNode(6)))
  near(six.nodes.get(portNode(5)).x - six.nodes.get(portNode(0)).x, 5 * BATCH_PORT_PITCH)
  assert.ok(six.route(stockerNode(0), portNode(5)).len > 0)
  assert.equal(buildTrack().ports, N_PORTS)
})

test('a batch asks for several vehicles at once and gets them one after another', () => {
  const vehicles = PARK.map(p => ({ park: p.id, busy: [] }))
  const picks = []
  for (let i = 0; i < 5; i++) {
    const f = earliestFree(track, vehicles, stockerNode(i), 1000, 40)
    picks.push(f)
    reserveVehicle(vehicles[f.vehicle], f.at, f.at + 40)
  }
  assert.equal(new Set(picks.slice(0, 3).map(p => p.vehicle)).size, 3, 'three vehicles, three lots at once')
  assert.ok(picks[3].at > 1000 && picks[4].at > 1000, 'the fourth and fifth wait for a vehicle to free up')
  assert.ok(picks.every(p => p.vehicle >= 0))
})

test('a batch is delayed past the decision, and departure phases shift with the pickup delay', () => {
  const a = planArrival(track, { lot: 'L', t0: 0, slot: stockerNode(0), port: 0, vehicle: 0, park: 'park0', delay: 20 })
  assert.equal(a.d1, DECIDE_S + 20)
  assert.equal(arrivalPhase(a, 10), 'select', 'chosen, waiting for its vehicle')
  near(decideProgress(a, 3), 0.5); assert.equal(decideProgress(a, 10), null, 'the decision window is over even while the lot waits')
  const d = planDeparture(track, { lot: 'L', tEnd: 0, port: 0, vehicle: 0, park: 'park0', delay: 30 })
  assert.equal(d.e2, 30); near(d.e1, 30 - PRE_S); near(d.e3, 30 + HOIST_S)
  assert.equal(departureAt(track, d, 5).phase, 'idle'); assert.equal(departureAt(track, d, 29).phase, 'wait')
  near(departureAt(track, d, 30 + HOIST_S / 2).foup.y, (PORT_Y + CARRY_Y) / 2)
})

test('compatible batch groups: same product, same step, largest first', () => {
  const steps = { 'Lot_2_1': { step: '358_Diffusion', bmin: 4, bmax: 5 }, 'Lot_2_2': { step: '358_Diffusion', bmin: 4, bmax: 5 },
                  'Lot_2_3': { step: '358_Diffusion' }, 'Lot_7_9': { step: '325_Diffusion', bmin: 5 }, 'Lot_2_8': { step: '325_Diffusion' } }
  const g = batchGroups(['Lot_2_1', 'Lot_7_9', 'Lot_2_2', 'Lot_2_8', 'Lot_2_3', 'Lot_9_9'], steps)
  assert.equal(g.length, 3)
  assert.deepEqual(g[0].lots, ['Lot_2_1', 'Lot_2_2', 'Lot_2_3']); assert.equal(g[0].part, 'part_2'); assert.equal(g[0].bmin, 4)
  assert.equal(partOf('Lot_10_9431'), 'part_10'); assert.equal(partOf('HotLot_3_2308'), 'part_3'); assert.equal(partOf('x'), null)
  assert.equal(statesFor('furnace')[1], 'WAITING FOR BATCH'); assert.equal(statesFor('etch')[1], 'LOT SELECTED')
  assert.equal(sceneKind('Diffusion_FE_94'), 'furnace')
  assert.equal(toolState({ newest: null, tau: null, processing: 0, online: true, kind: 'furnace', gathering: true }), 'WAITING FOR BATCH')
  assert.equal(toolState({ newest: null, tau: null, processing: 0, online: true, kind: 'furnace', gathering: false }), 'IDLE')
})

test('an arrival plays select -> reserve -> transit -> load -> process in fab seconds', () => {
  const job = planArrival(track, { lot: 'Lot_6_5459', t0: 1000, slot: stockerNode(2), port: 1, vehicle: 0, park: 'park0' })
  assert.ok(job.d1 < job.d2 && job.d2 < job.d3 && job.d3 < job.d4 && job.d4 < job.d5 && job.d5 < job.d6)
  assert.equal(job.d1, DECIDE_S)
  near(job.d3 - job.d2, HOIST_S); near(job.d5 - job.d4, HOIST_S)
  near(job.d2 - job.d1, track.route('park0', stockerNode(2)).len / V)
  // A near pickup delivers in well under half a minute of fab time.
  assert.ok(job.d5 < 30, `near delivery took ${job.d5}s`)
  assert.equal(arrivalPhase(job, 0), 'select')
  assert.equal(arrivalPhase(job, job.d1), 'reserve')
  assert.equal(arrivalPhase(job, job.d3), 'transit')
  assert.equal(arrivalPhase(job, job.d4), 'load')
  assert.equal(arrivalPhase(job, job.d5), 'process')
  assert.equal(arrivalPhase(job, 1e9), 'process')

  const slot = slotPos(track, stockerNode(2)), port = slotPos(track, portNode(1))
  const a0 = arrivalAt(track, job, 0)
  assert.deepEqual(a0.foup, slot); assert.equal(a0.vehicle, null)
  const aHoist = arrivalAt(track, job, job.d2 + HOIST_S / 2)
  near(aHoist.foup.y, (SLOT_Y + CARRY_Y) / 2); assert.ok(aHoist.carrying)
  const aTransit = arrivalAt(track, job, (job.d3 + job.d4) / 2)
  near(aTransit.foup.y, CARRY_Y); near(aTransit.foup.x, aTransit.vehicle.x)
  const aDone = arrivalAt(track, job, job.d5 + 0.01)
  assert.deepEqual(aDone.foup, port); assert.ok(aDone.vehicle, 'vehicle is still driving back')
  const aBack = arrivalAt(track, job, job.d6 + 1)
  assert.equal(aBack.vehicle, null)
})

test('a departure pre-positions the vehicle, holds it over the port, then carries the FOUP out', () => {
  const job = planDeparture(track, { lot: 'Lot_5_4509', tEnd: 5000, port: 0, vehicle: 0, park: 'park0' })
  assert.ok(job.e0 < job.e1 && job.e1 < 0 && 0 < job.e3 && job.e3 < job.e4 && job.e4 < job.e5)
  near(job.e1, -PRE_S)
  assert.equal(departureAt(track, job, job.e0 - 1).phase, 'idle')
  assert.equal(departureAt(track, job, (job.e0 + job.e1) / 2).phase, 'approach')
  const w = departureAt(track, job, -1)
  assert.equal(w.phase, 'wait'); near(w.vehicle.x, track.nodes.get(portNode(0)).x)
  const u = departureAt(track, job, HOIST_S / 2)
  assert.equal(u.phase, 'unload'); near(u.foup.y, (PORT_Y + CARRY_Y) / 2)
  const x = departureAt(track, job, (job.e3 + job.e4) / 2)
  assert.equal(x.phase, 'exit'); assert.ok(x.carrying)
  const g = departureAt(track, job, job.e4 + 1)
  assert.equal(g.phase, 'gone'); assert.equal(g.foup, null)
  assert.equal(departureAt(track, job, job.e5 + 1).phase, 'done')
})

test('tool state follows the newest arrival, then what is on the tool', () => {
  const job = planArrival(track, { lot: 'L', t0: 0, slot: stockerNode(0), port: 0, vehicle: 0, park: 'park0' })
  assert.equal(toolState({ newest: job, tau: 1, processing: 1, online: true }), 'LOT SELECTED')
  assert.equal(toolState({ newest: job, tau: job.d3 + 0.1, processing: 1, online: true }), 'FOUP IN TRANSIT')
  assert.equal(toolState({ newest: job, tau: job.d5 + 1, processing: 1, online: true }), 'PROCESSING')
  assert.equal(toolState({ newest: null, tau: null, processing: 0, online: true }), 'IDLE')
  assert.equal(toolState({ newest: job, tau: 1, processing: 0, online: false }), 'DOWN')
  assert.equal(decideProgress(job, -1), null)
  near(decideProgress(job, DECIDE_S / 2), 0.5)
  assert.equal(decideProgress(job, DECIDE_S), null)
})

test('rail distance tells the stocker shelf from the buffer', () => {
  const s = railDistance(track, stockerNode(7), 1), u = railDistance(track, utsNode(0), 1)
  assert.ok(s < 10 && u > 30, `${s} ${u}`)
  assert.equal(railDistance(track, null, 1), null)
})

test('a lot finds the decision that sent it to the tool, newest first', () => {
  const mk = (day, lot) => ({ day, why: { chosen: [{ lot }], alternatives: [] } })
  const ds = [mk('90.3', 'Lot_1_1'), mk('90.2', 'Lot_1_2'), { day: '90.1' }, mk('90.0', 'Lot_1_2')]
  assert.equal(decisionForLot(ds, 'Lot_1_2').day, '90.2')
  assert.equal(decisionForLot(ds, 'Lot_9_9'), null)
  assert.equal(decisionForLot(null, 'Lot_1_1'), null)
})

test('dry etch and planarisation get scenes; other families do not', () => {
  assert.equal(sceneKind('DE_FE_86'), 'etch'); assert.equal(sceneKind('DE_BE_11'), 'etch')
  assert.equal(sceneKind('Planar_BE_75'), 'cmp'); assert.equal(sceneKind('Planar_FE_79'), 'cmp')
  assert.equal(sceneKind('WE_FE_108'), null); assert.equal(sceneKind(null), null)
  assert.ok(isSceneFamily('Planar_FE_77') && !isSceneFamily('WE_FE_108'))
})

test('a changeover holds the tool between loading and processing, and is read from the decision tuple', () => {
  const job = planArrival(track, { lot: 'L', t0: 0, slot: stockerNode(0), port: 0, vehicle: 0, park: 'park0', changeover: 900 })
  assert.equal(arrivalPhase(job, job.d5 + 1), 'changeover')
  assert.equal(arrivalPhase(job, job.d5 + 899), 'changeover')
  assert.equal(arrivalPhase(job, job.d5 + 900), 'process')
  near(changeoverProgress(job, job.d5 + 450), 0.5); assert.equal(changeoverProgress(job, job.d5 + 950), null)
  assert.equal(toolState({ newest: job, tau: job.d5 + 10, processing: 0, online: true, kind: 'litho' }), 'SETUP CHANGE')
  const plain = planArrival(track, { lot: 'L', t0: 0, slot: stockerNode(0), port: 0, vehicle: 0, park: 'park0' })
  assert.equal(arrivalPhase(plain, plain.d5 + 1), 'process'); assert.equal(changeoverProgress(plain, plain.d5 + 1), null)
  const d = { why: { keys: ['gate', 'setup_s', '-prio', 'free_since', 'deadline'], chosen: [
    { lot: 'A', setup_match: false, tuple: [0, 720, -1, 5, 9] }, { lot: 'B', setup_match: true, tuple: [0, 480, -1, 5, 9] }, { lot: 'D', setup_match: false, tuple: [0, 0, -1, 5, 9] }] } }
  assert.equal(setupCostOf(d, 'A'), 720, 'a switch costs what the tuple priced')
  assert.equal(setupCostOf(d, 'B'), 480, 'the flag is stamped after the switch; the priced tuple is what counts')
  assert.equal(setupCostOf(d, 'D'), 0); assert.equal(setupCostOf(d, 'C'), 0); assert.equal(setupCostOf(null, 'A'), 0)
  assert.equal(setupBefore([{ setup: 'SU2' }, { setup: 'SU1' }], 0), 'SU1')
  assert.equal(setupBefore([{ setup: 'SU2' }, { setup: '-', why: { tool_setup: 'SU0' } }], 0), 'SU0')
  assert.equal(setupBefore([{ setup: 'SU2' }], 0), null)
  assert.equal(statesFor('litho')[5], 'SETUP CHANGE'); assert.equal(sceneKind('LithoTrack_FE_115'), 'litho'); assert.equal(sceneKind('Litho_FE_92'), 'litho')
  assert.equal(sceneKind('LithoMet_FE_19'), null); assert.equal(sceneKind('Litho_REG_FE_64'), null)
  assert.equal(setupHue('-'), null); assert.equal(setupHue('SU036_1'), setupHue('SU036_1')); assert.ok(setupHue('SU036_1') >= 0 && setupHue('SU036_1') < 360)
})
