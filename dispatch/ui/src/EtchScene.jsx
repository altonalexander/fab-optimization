import { useEffect, useRef, useState } from 'react'
import * as THREE from 'three'
import { OrbitControls } from 'three/addons/controls/OrbitControls.js'
import { CSS2DRenderer, CSS2DObject } from 'three/addons/renderers/CSS2DRenderer.js'
import { simNow, shortId, fmtCountdown, setupLabel } from './toolflow_geom.js'
import * as G from './etch_geom.js'

// ---------------------------------------------------------------------------
// One dry-etch tool in three dimensions: its load ports, the lots waiting for
// its family on the track-side shelves, and the overhead vehicle that carries
// the chosen lot over when a dispatch decision lands.
//
// Everything moves on the FAB clock. The vehicle drives at 4 m/s of simulated
// time, so at 1x playback a delivery takes the twenty-odd real seconds it
// would take in a fab, at 20x it is a two-second flick, and at 400x the FOUP
// is simply on the port by the next frame. That is deliberate and matches
// every other view here: the scene is a rendering of fab time, not a film
// that plays at its own pace. The clock chip says so and tells you to drop to
// 1x to watch.
//
// The model (which vehicle, which route, what phase at what instant) lives in
// etch_geom.js; this file owns meshes, labels and the overlays. The scene is
// a visualisation of the decision, not a second simulation: SMT2020 has no
// AMHS, so ports, shelves and vehicles are ours, and the legend says so.
// ---------------------------------------------------------------------------

const C = {
  bg: 0x0b1220, floor: 0x121a2c, gridA: 0x1d2a44, gridB: 0x172238,
  toolBody: 0x263248, toolEfem: 0x2f3f5e, toolEdge: 0x3a4b6b,
  focalBody: 0x3b4d70, focalEfem: 0x4a5f88, focalEdge: 0x8ea3cf,
  rail: 0x8a9ab8, hanger: 0x2c3a58, shelf: 0x33415f, arrow: 0x5d6f93,
  foupWait: 0x9aa8bf, foupLid: 0x1f2937, foupHot: 0xef4444, foupSel: 0x22d3ee,
  foupOn: 0x22c55e, foupLeave: 0xa78bfa, foupTempt: 0xf87171, foupGather: 0xf59e0b,
  vehicle: 0xe2e8f0, ledIdle: 0x64748b, ledAssigned: 0x22d3ee, ledCarry: 0x22c55e, ledDepart: 0xa78bfa,
  route: 0x22d3ee, ghost: 0xf87171,
}
const LAMP = {
  IDLE: 0x64748b, 'LOT SELECTED': 0xf59e0b, 'BATCH SELECTED': 0xf59e0b, 'WAITING FOR BATCH': 0xa16207, RESERVED: 0xf59e0b, 'FOUP IN TRANSIT': 0x22d3ee,
  'SETUP CHANGE': 0xc084fc,
  LOADING: 0x22d3ee, PROCESSING: 0x22c55e, DOWN: 0xef4444,
}
const VIEW_W = 47            // metres across the viewport at zoom 1
const FOUP_SCALE = 1.6       // FOUPs and vehicles are drawn oversize so they read at this scale
const FOUP_H = 0.48 * FOUP_SCALE
const NEIGHBOURS_FRONT = [-13.5, -9, -4.5, -0.5, 9.5, 14]
const NEIGHBOURS_BACK = [-13.5, -9, -4.5, 0, 4.5, 9, 13.5]

const std = (color, extra = {}) => new THREE.MeshStandardMaterial({ color, roughness: 0.85, metalness: 0.1, ...extra })

function box(w, h, d, color, edgeColor) {
  const g = new THREE.BoxGeometry(w, h, d)
  const m = new THREE.Mesh(g, std(color))
  m.position.y = h / 2
  if (edgeColor != null) {
    m.add(new THREE.LineSegments(new THREE.EdgesGeometry(g),
      new THREE.LineBasicMaterial({ color: edgeColor, transparent: true, opacity: 0.75 })))
  }
  return m
}

function label(text, cls, x, y, z) {
  const el = document.createElement('div')
  el.className = `etch-lbl ${cls || ''}`
  el.textContent = text
  const o = new CSS2DObject(el)
  o.position.set(x, y, z)
  o.userData.el = el
  return o
}

const setText = (o, s) => { if (o.userData.el.textContent !== s) o.userData.el.textContent = s }
const setCls = (o, c) => { if (o.userData.el.className !== c) o.userData.el.className = c }

// A neighbouring tool: body behind, a low front-end module facing the rail.
// `s` is -1 for the row behind the front rail, +1 behind the back rail.
function neighbourTool(s, id) {
  const g = new THREE.Group()
  const tall = s < 0
  const efem = box(3.0, tall ? 1.8 : 1.5, 0.8, C.toolEfem, C.toolEdge); efem.position.z = s * 1.0
  const body = box(4.4, tall ? 3.0 : 2.4, 4.4, C.toolBody, C.toolEdge); body.position.z = s * 3.6
  const lamp = new THREE.Mesh(new THREE.SphereGeometry(0.12, 10, 8), std(C.ledIdle, { emissive: C.ledIdle, emissiveIntensity: 0.3 }))
  lamp.position.set(1.2, (tall ? 1.8 : 1.5) + 0.25, s * 1.0)
  const lbl = label(id || '', 'etch-lbl-tool', 0, (tall ? 3.0 : 2.4) + 0.5, s * 3.6)
  g.add(efem, body, lamp, lbl)
  g.userData.label = lbl
  return g
}

// The focal tool: a cluster etch tool. Three load ports under the rail, a
// front-end module behind them, then the transfer chamber with four process
// chambers around it. A status lamp on a mast and a ring on the floor carry
// the machine state; the lamp is what you would look for across a real bay.
function focalTool(id, family) {
  const g = new THREE.Group()
  for (let i = 0; i < G.N_PORTS; i++) {
    const x = (i - (G.N_PORTS - 1) / 2) * G.PORT_PITCH
    const ped = box(0.72, G.PORT_Y - 0.02, 0.72, C.focalEfem, C.focalEdge)
    ped.position.x = x
    g.add(ped, label(`LP${i + 1}`, 'etch-lbl-port', x, 0.35, 0.5))
  }
  const efem = box(3.9, 1.9, 0.9, C.focalEfem, C.focalEdge); efem.position.z = -1.05
  const base = box(5.4, 0.25, 4.7, C.focalBody, C.focalEdge); base.position.z = -3.85
  const xfer = new THREE.Mesh(new THREE.CylinderGeometry(1.35, 1.35, 1.6, 8), std(C.focalBody))
  xfer.position.set(0, 0.25 + 0.8, -3.7)
  xfer.add(new THREE.LineSegments(new THREE.EdgesGeometry(xfer.geometry), new THREE.LineBasicMaterial({ color: C.focalEdge, transparent: true, opacity: 0.7 })))
  g.add(efem, base, xfer)
  for (const [cx, cz] of [[-1.95, -2.7], [1.95, -2.7], [-1.95, -4.8], [1.95, -4.8]]) {
    const ch = new THREE.Mesh(new THREE.CylinderGeometry(0.78, 0.78, 1.5, 8), std(C.focalEfem))
    ch.position.set(cx, 0.25 + 0.75, cz)
    ch.add(new THREE.LineSegments(new THREE.EdgesGeometry(ch.geometry), new THREE.LineBasicMaterial({ color: C.focalEdge, transparent: true, opacity: 0.7 })))
    g.add(ch)
  }
  const mast = new THREE.Mesh(new THREE.CylinderGeometry(0.04, 0.04, 0.7, 6), std(C.rail))
  mast.position.set(1.75, 1.9 + 0.35, -1.05)
  const lampMat = std(C.ledIdle, { emissive: C.ledIdle, emissiveIntensity: 0.8 })
  const lamp = new THREE.Mesh(new THREE.SphereGeometry(0.18, 12, 10), lampMat)
  lamp.position.set(1.75, 1.9 + 0.7 + 0.15, -1.05)
  const ringMat = new THREE.MeshBasicMaterial({ color: C.ledIdle, transparent: true, opacity: 0.45, side: THREE.DoubleSide })
  const ring = new THREE.Mesh(new THREE.RingGeometry(3.7, 3.95, 56), ringMat)
  ring.rotation.x = -Math.PI / 2
  ring.position.set(0, 0.02, -3.3)
  const name = label(id, 'etch-lbl-focal', 0, 5.4, -4.2)
  const sub = label(family, 'etch-lbl-focal-sub', 0, 4.7, -4.2)
  const kind = label(G.KIND_LABEL.etch, 'etch-lbl-focal-sub etch-lbl-dim', 0, 4.1, -4.2)
  g.add(mast, lamp, ring, name, sub, kind)
  g.userData = { lampMat, ringMat, name, sub, spin: [] }
  return g
}

// The CMP polisher: load ports and a front-end module like the etch tool,
// then a polishing deck with three platens under their heads on a carousel,
// a post-CMP cleaner across the back and a slurry cabinet. The platens turn
// while a lot is processing, on the fab clock, so they stand still when the
// fab is paused like everything else.
function cmpTool(id, family) {
  const g = new THREE.Group()
  const deckC = 0x35505f, platenC = 0x6b8fa3, padC = 0x203845, edge = 0x8fb3c7
  for (let i = 0; i < G.N_PORTS; i++) {
    const x = (i - (G.N_PORTS - 1) / 2) * G.PORT_PITCH
    const ped = box(0.72, G.PORT_Y - 0.02, 0.72, C.focalEfem, C.focalEdge)
    ped.position.x = x
    g.add(ped, label(`LP${i + 1}`, 'etch-lbl-port', x, 0.35, 0.5))
  }
  const efem = box(3.9, 1.9, 0.9, C.focalEfem, C.focalEdge); efem.position.z = -1.05
  const deck = box(5.4, 1.1, 3.2, deckC, edge); deck.position.z = -3.2
  g.add(efem, deck)
  const spin = []
  const carousel = new THREE.Mesh(new THREE.CylinderGeometry(0.22, 0.22, 1.3, 10), std(0x94a3b8))
  carousel.position.set(0, 1.1 + 0.65, -3.2)
  g.add(carousel)
  const arm = new THREE.Mesh(new THREE.BoxGeometry(4.0, 0.12, 0.3), std(0x94a3b8))
  arm.position.set(0, 1.1 + 1.25, -3.2)
  const armGroup = new THREE.Group(); armGroup.position.set(0, 1.1 + 1.25, -3.2)
  arm.position.set(0, 0, 0); armGroup.add(arm)
  for (const cx of [-1.75, 0, 1.75]) {
    const platen = new THREE.Mesh(new THREE.CylinderGeometry(0.78, 0.78, 0.14, 24), std(platenC))
    platen.position.set(cx, 1.1 + 0.07, -3.2)
    const pad = new THREE.Mesh(new THREE.CylinderGeometry(0.72, 0.72, 0.03, 24), std(padC, { roughness: 1 }))
    pad.position.set(cx, 1.1 + 0.155, -3.2)
    // A spoke on the pad so the turn reads as motion.
    const spoke = new THREE.Mesh(new THREE.BoxGeometry(1.3, 0.005, 0.06), std(0x3f6273))
    spoke.position.set(cx, 1.1 + 0.175, -3.2)
    const head = new THREE.Mesh(new THREE.CylinderGeometry(0.34, 0.34, 0.22, 16), std(0xcbd5e1, { roughness: 0.5 }))
    head.position.set(cx, -0.35, 0)
    const stem = new THREE.Mesh(new THREE.CylinderGeometry(0.05, 0.05, 0.5, 6), std(0x94a3b8))
    stem.position.set(cx, -0.15, 0)
    armGroup.add(head, stem)
    g.add(platen, pad, spoke)
    spin.push(pad, spoke)
  }
  g.add(armGroup)
  const cleaner = box(5.4, 2.0, 1.5, deckC, edge); cleaner.position.z = -5.55
  const slurry = box(0.9, 1.6, 0.9, 0x2b3f4c, edge); slurry.position.set(2.25, 0, -1.6 - 0.3)
  g.add(cleaner, slurry)
  const mast = new THREE.Mesh(new THREE.CylinderGeometry(0.04, 0.04, 0.7, 6), std(C.rail))
  mast.position.set(1.75, 1.9 + 0.35, -1.05)
  const lampMat = std(C.ledIdle, { emissive: C.ledIdle, emissiveIntensity: 0.8 })
  const lamp = new THREE.Mesh(new THREE.SphereGeometry(0.18, 12, 10), lampMat)
  lamp.position.set(1.75, 1.9 + 0.7 + 0.15, -1.05)
  const ringMat = new THREE.MeshBasicMaterial({ color: C.ledIdle, transparent: true, opacity: 0.45, side: THREE.DoubleSide })
  const ring = new THREE.Mesh(new THREE.RingGeometry(3.7, 3.95, 56), ringMat)
  ring.rotation.x = -Math.PI / 2
  ring.position.set(0, 0.02, -3.3)
  const name = label(id, 'etch-lbl-focal', 0, 5.4, -4.2)
  const sub = label(family, 'etch-lbl-focal-sub', 0, 4.7, -4.2)
  const kind = label(G.KIND_LABEL.cmp, 'etch-lbl-focal-sub etch-lbl-dim', 0, 4.1, -4.2)
  g.add(mast, lamp, ring, name, sub, kind)
  g.userData = { lampMat, ringMat, name, sub, spin, armGroup }
  return g
}

// The vertical diffusion furnace: a load station with six ports across the
// front (one per FOUP of the largest batch), the tall cabinet behind it, two
// tubes on top that glow while a batch runs, and a gas cabinet at the side.
function furnaceTool(id, family) {
  const g = new THREE.Group()
  const bodyC = 0x4a4560, edge = 0xa89ccf
  for (let i = 0; i < G.BATCH_PORTS; i++) {
    const x = (i - (G.BATCH_PORTS - 1) / 2) * G.BATCH_PORT_PITCH
    const ped = box(0.6, G.PORT_Y - 0.02, 0.6, C.focalEfem, C.focalEdge)
    ped.position.x = x
    g.add(ped, label(`LP${i + 1}`, 'etch-lbl-port', x, 0.35, 0.5))
  }
  const station = box(5.4, 1.9, 0.9, C.focalEfem, C.focalEdge); station.position.z = -1.05
  const cabinet = box(5.4, 3.6, 3.0, bodyC, edge); cabinet.position.z = -3.2
  g.add(station, cabinet)
  const tubeMat = std(0x3b3650, { emissive: 0xf59e0b, emissiveIntensity: 0.05 })
  for (const cx of [-1.3, 1.3]) {
    const tube = new THREE.Mesh(new THREE.CylinderGeometry(0.5, 0.5, 1.4, 20), tubeMat)
    tube.position.set(cx, 3.6 + 0.7, -3.2)
    const cap = new THREE.Mesh(new THREE.CylinderGeometry(0.58, 0.58, 0.12, 20), std(0x94a3b8))
    cap.position.set(cx, 3.6 + 1.4 + 0.06, -3.2)
    g.add(tube, cap)
  }
  const gas = box(0.9, 2.2, 0.9, 0x2b3f4c, edge); gas.position.set(-2.25, 0, -1.6 - 0.3)
  g.add(gas)
  const mast = new THREE.Mesh(new THREE.CylinderGeometry(0.04, 0.04, 0.7, 6), std(C.rail))
  mast.position.set(2.4, 1.9 + 0.35, -1.05)
  const lampMat = std(C.ledIdle, { emissive: C.ledIdle, emissiveIntensity: 0.8 })
  const lamp = new THREE.Mesh(new THREE.SphereGeometry(0.18, 12, 10), lampMat)
  lamp.position.set(2.4, 1.9 + 0.7 + 0.15, -1.05)
  const ringMat = new THREE.MeshBasicMaterial({ color: C.ledIdle, transparent: true, opacity: 0.45, side: THREE.DoubleSide })
  const ring = new THREE.Mesh(new THREE.RingGeometry(3.7, 3.95, 56), ringMat)
  ring.rotation.x = -Math.PI / 2
  ring.position.set(0, 0.02, -3.3)
  const name = label(id, 'etch-lbl-focal', 0, 6.4, -4.0)
  const sub = label(family, 'etch-lbl-focal-sub', 0, 5.7, -4.0)
  const kind = label(G.KIND_LABEL.furnace, 'etch-lbl-focal-sub etch-lbl-dim', 0, 5.1, -4.0)
  g.add(mast, lamp, ring, name, sub, kind)
  g.userData = { lampMat, ringMat, name, sub, spin: [], tubeMat }
  return g
}

// The litho cell: the coat/develop track across the front (a row of process
// modules on its deck) with the scanner behind it, its column on top. A band
// along the track's front edge wears the colour of the setup it is on.
function lithoTool(id, family) {
  const g = new THREE.Group()
  const trackC = 0x2f4858, scanC = 0x3d4a66, edge = 0x9fb8cc
  for (let i = 0; i < G.N_PORTS; i++) {
    const x = (i - (G.N_PORTS - 1) / 2) * G.PORT_PITCH
    const ped = box(0.72, G.PORT_Y - 0.02, 0.72, C.focalEfem, C.focalEdge)
    ped.position.x = x
    g.add(ped, label(`LP${i + 1}`, 'etch-lbl-port', x, 0.35, 0.5))
  }
  const trackBody = box(5.4, 2.0, 2.4, trackC, edge); trackBody.position.z = -1.8
  g.add(trackBody)
  for (let i = 0; i < 5; i++) {
    const mod = box(0.8, 0.5, 1.6, 0x3b5566, edge); mod.position.set(-2.0 + i * 1.0, 2.0, -1.8)
    g.add(mod)
  }
  const bandMat = std(0x64748b, { emissive: 0x64748b, emissiveIntensity: 0.35 })
  const band = new THREE.Mesh(new THREE.BoxGeometry(5.3, 0.16, 0.12), bandMat)
  band.position.set(0, 1.85, -0.62)
  g.add(band)
  const scanner = box(4.8, 3.2, 2.8, scanC, edge); scanner.position.z = -4.6
  const column = new THREE.Mesh(new THREE.CylinderGeometry(0.55, 0.7, 1.3, 20), std(0x94a3b8, { roughness: 0.4 }))
  column.position.set(0.6, 3.2 + 0.65, -4.6)
  const cab = box(1.2, 2.6, 1.2, 0x2b3f4c, edge); cab.position.set(-2.6 + 0.6 - 0.9, 0, -4.6)
  g.add(scanner, column)
  const mast = new THREE.Mesh(new THREE.CylinderGeometry(0.04, 0.04, 0.7, 6), std(C.rail))
  mast.position.set(2.4, 2.0 + 0.35, -0.9)
  const lampMat = std(C.ledIdle, { emissive: C.ledIdle, emissiveIntensity: 0.8 })
  const lamp = new THREE.Mesh(new THREE.SphereGeometry(0.18, 12, 10), lampMat)
  lamp.position.set(2.4, 2.0 + 0.7 + 0.15, -0.9)
  const ringMat = new THREE.MeshBasicMaterial({ color: C.ledIdle, transparent: true, opacity: 0.45, side: THREE.DoubleSide })
  const ring = new THREE.Mesh(new THREE.RingGeometry(3.7, 3.95, 56), ringMat)
  ring.rotation.x = -Math.PI / 2
  ring.position.set(0, 0.02, -3.3)
  const name = label(id, 'etch-lbl-focal', 0, 5.9, -4.6)
  const sub = label(family, 'etch-lbl-focal-sub', 0, 5.2, -4.6)
  const kind = label(G.KIND_LABEL.litho, 'etch-lbl-focal-sub etch-lbl-dim', 0, 4.6, -4.6)
  const setupLbl = label('', 'etch-lbl-setup', 0, 2.35, -0.62)
  g.add(mast, lamp, ring, name, sub, kind, setupLbl)
  g.userData = { lampMat, ringMat, name, sub, spin: [], bandMat, setupLbl }
  return g
}

const hueColor = (hue, l = 0.55) => hue == null ? null : new THREE.Color().setHSL(hue / 360, 0.65, l)

function foupMesh() {
  const g = new THREE.Group()
  const bodyMat = std(C.foupWait, { emissive: 0x000000, emissiveIntensity: 0.6 })
  const body = new THREE.Mesh(new THREE.BoxGeometry(0.45, 0.42, 0.45), bodyMat); body.position.y = 0.21
  const lidMat = std(C.foupLid)
  const lid = new THREE.Mesh(new THREE.BoxGeometry(0.47, 0.06, 0.47), lidMat); lid.position.y = 0.45
  const handle = new THREE.Mesh(new THREE.BoxGeometry(0.22, 0.09, 0.14), std(C.foupLid)); handle.position.y = 0.52
  g.add(body, lid, handle)
  g.scale.setScalar(FOUP_SCALE)
  const lbl = label('', 'etch-lbl-lot', 0, 0.62, 0)
  g.add(lbl)
  g.userData = { bodyMat, lidMat, lbl }
  return g
}

// An overhead hoist vehicle: a body hanging under the rail, a status LED, and
// a belt with a gripper that pays out to whatever it is carrying.
function vehicleMesh(name) {
  const g = new THREE.Group()
  const body = new THREE.Mesh(new THREE.BoxGeometry(1.25, 0.5, 0.72), std(C.vehicle, { roughness: 0.5 })); body.position.y = -0.45
  const clamp = new THREE.Mesh(new THREE.BoxGeometry(0.6, 0.22, 0.34), std(0x94a3b8)); clamp.position.y = -0.1
  const ledMat = std(C.ledIdle, { emissive: C.ledIdle, emissiveIntensity: 1 })
  const led = new THREE.Mesh(new THREE.BoxGeometry(0.16, 0.1, 0.74), ledMat); led.position.set(0.55, -0.3, 0)
  const belt = new THREE.Mesh(new THREE.CylinderGeometry(0.025, 0.025, 1, 6), std(0xcbd5e1))
  const grip = new THREE.Mesh(new THREE.BoxGeometry(0.62, 0.1, 0.62), std(0x94a3b8))
  g.add(body, clamp, led, belt, grip)
  const lbl = label(name, 'etch-lbl-oht', 0, 0.45, 0)
  g.add(lbl)
  g.userData = { ledMat, belt, grip, lbl }
  return g
}

// Rail as a tube through the loop's points, plus the interbay spur.
function railMeshes(track) {
  const pts = track.loop.map(p => new THREE.Vector3(p.x, G.RAIL_Y, p.z))
  const path = new THREE.CurvePath()
  for (let i = 0; i < pts.length; i++) path.add(new THREE.LineCurve3(pts[i], pts[(i + 1) % pts.length]))
  const mat = std(C.rail, { metalness: 0.45, roughness: 0.45 })
  const loop = new THREE.Mesh(new THREE.TubeGeometry(path, 260, 0.13, 8, true), mat)
  const apex = track.nodes.get(track.apex), exit = track.nodes.get('exit')
  const spur = new THREE.Mesh(new THREE.TubeGeometry(
    new THREE.LineCurve3(new THREE.Vector3(apex.x, G.RAIL_Y, apex.z), new THREE.Vector3(exit.x, G.RAIL_Y, exit.z)), 2, 0.13, 8, false), mat)
  const g = new THREE.Group()
  g.add(loop, spur)
  // Hangers every few metres on the straights, up to an implied ceiling.
  const hMat = std(C.hanger)
  for (const z of [G.FRONT_Z, G.BACK_Z]) {
    for (let x = -G.L + 1; x <= G.L - 1; x += 5) {
      const h = new THREE.Mesh(new THREE.CylinderGeometry(0.03, 0.03, 2.6, 5), hMat)
      h.position.set(x, G.RAIL_Y + 1.3, z)
      g.add(h)
    }
  }
  // Direction cones: the loop is one-way, and that is why some routes are long.
  const cone = new THREE.ConeGeometry(0.22, 0.6, 8)
  const cMat = std(C.arrow, { emissive: C.arrow, emissiveIntensity: 0.4 })
  const arrow = (x, z, dir) => {
    const m = new THREE.Mesh(cone, cMat)
    m.position.set(x, G.RAIL_Y + 0.35, z)
    m.quaternion.setFromUnitVectors(new THREE.Vector3(0, 1, 0), new THREE.Vector3(dir, 0, 0))
    g.add(m)
  }
  for (const x of [-1, 9]) arrow(x, G.FRONT_Z, 1)
  for (const x of [-3, 8]) arrow(x, G.BACK_Z, -1)
  return g
}

function shelves(track) {
  const g = new THREE.Group()
  const mat = std(C.shelf)
  const post = new THREE.CylinderGeometry(0.03, 0.03, G.RAIL_Y - G.SLOT_Y, 5)
  const plate = new THREE.BoxGeometry(0.9, 0.07, 0.9)
  const add = id => {
    const p = G.slotPos(track, id)
    const n = track.nodes.get(id)
    const m = new THREE.Mesh(plate, mat); m.position.set(p.x, p.y - 0.035, p.z)
    const s = new THREE.Mesh(post, mat); s.position.set(p.x, (G.RAIL_Y + G.SLOT_Y) / 2, n.z + (p.z - n.z) * 0.45)
    g.add(m, s)
  }
  G.STOCKER_X.forEach((_, i) => add(G.stockerNode(i)))
  G.UTS_X.forEach((_, i) => add(G.utsNode(i)))
  const sx = (G.STOCKER_X[0] + G.STOCKER_X[G.STOCKER_X.length - 1]) / 2
  const ux = (G.UTS_X[0] + G.UTS_X[G.UTS_X.length - 1]) / 2
  // Zone names sit above the rail, clear of the lot labels on the shelves.
  g.add(label('STOCKER · track-side shelf', 'etch-lbl-zone', sx, G.RAIL_Y + 1.9, G.FRONT_Z))
  g.add(label('UNDER-TRACK BUFFER', 'etch-lbl-zone', ux, G.RAIL_Y + 1.9, G.BACK_Z))
  g.add(label('→ interbay', 'etch-lbl-zone', G.EXIT_X - 1.5, G.RAIL_Y + 0.9, 0))
  return g
}

function routeTube(route) {
  const pts = route.pts.map(p => new THREE.Vector3(p.x, G.RAIL_Y - 0.28, p.z))
  const path = new THREE.CurvePath()
  for (let i = 1; i < pts.length; i++) path.add(new THREE.LineCurve3(pts[i - 1], pts[i]))
  return new THREE.TubeGeometry(path, Math.max(8, pts.length * 6), 0.17, 6, false)
}

function dashedLine(color) {
  const geo = new THREE.BufferGeometry().setFromPoints([new THREE.Vector3(), new THREE.Vector3()])
  const line = new THREE.Line(geo, new THREE.LineDashedMaterial({ color, dashSize: 0.6, gapSize: 0.35, transparent: true, opacity: 0.85 }))
  line.visible = false
  return line
}
function setDashed(line, route) {
  if (!route) { line.visible = false; line.userData.route = null; return }
  if (line.userData.route === route) { line.visible = true; return }
  line.userData.route = route
  const pts = route.pts.map(p => new THREE.Vector3(p.x, G.RAIL_Y - 0.42, p.z))
  line.geometry.dispose()
  line.geometry = new THREE.BufferGeometry().setFromPoints(pts)
  line.computeLineDistances()
  line.visible = true
}

const setMat = (mat, hex, emissive = null, intensity = 0) => {
  mat.color.setHex(hex)
  if (mat.emissive) { mat.emissive.setHex(emissive == null ? hex : emissive); mat.emissiveIntensity = intensity }
}

// ---------------------------------------------------------------------------
// The world: three.js objects plus the visualisation's own state (where each
// lot sits, which port and vehicle it got). Created once per mount.
// ---------------------------------------------------------------------------
function createWorld(host, toolId, family, kind) {
  const nPorts = kind === 'furnace' ? G.BATCH_PORTS : G.N_PORTS
  const track = G.buildTrack(kind === 'furnace' ? { ports: G.BATCH_PORTS, pitch: G.BATCH_PORT_PITCH } : {})
  const scene = new THREE.Scene()
  scene.background = new THREE.Color(C.bg)
  const camera = new THREE.OrthographicCamera(-1, 1, 1, -1, 0.1, 500)
  const renderer = new THREE.WebGLRenderer({ antialias: true, powerPreference: 'low-power' })
  renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2))
  renderer.domElement.className = 'etch-gl'
  host.appendChild(renderer.domElement)
  const labels = new CSS2DRenderer()
  labels.domElement.className = 'etch-labels'
  host.appendChild(labels.domElement)

  // Framed so the loop sits mid-frame with room on the right for the panel.
  const target = new THREE.Vector3(6.5, 1.0, -1.6)
  const az = 0.55, el = 0.72, dist = 120
  camera.position.set(target.x + dist * Math.sin(az) * Math.cos(el), target.y + dist * Math.sin(el), target.z + dist * Math.cos(az) * Math.cos(el))
  camera.lookAt(target)
  const controls = new OrbitControls(camera, renderer.domElement)
  controls.target.copy(target)
  controls.enablePan = false
  controls.enableDamping = true
  controls.dampingFactor = 0.08
  controls.minZoom = 0.8
  controls.maxZoom = 3.2
  controls.minPolarAngle = 0.35
  controls.maxPolarAngle = 1.3
  controls.update()
  controls.saveState()

  scene.add(new THREE.HemisphereLight(0xa9bbe0, 0x0b1220, 0.95))
  const sun = new THREE.DirectionalLight(0xffffff, 1.35)
  sun.position.set(30, 60, 25)
  scene.add(sun)

  const floor = new THREE.Mesh(new THREE.PlaneGeometry(140, 80), std(C.floor, { roughness: 1 }))
  floor.rotation.x = -Math.PI / 2
  scene.add(floor)
  const grid = new THREE.GridHelper(140, 70, C.gridA, C.gridB)
  grid.position.y = 0.01
  scene.add(grid)

  scene.add(railMeshes(track))
  scene.add(shelves(track))

  const neighbours = []
  for (const x of NEIGHBOURS_FRONT) { const m = neighbourTool(-1); m.position.set(x, 0, G.FRONT_Z); scene.add(m); neighbours.push(m) }
  for (const x of NEIGHBOURS_BACK) { const m = neighbourTool(1); m.position.set(x, 0, G.BACK_Z); scene.add(m); neighbours.push(m) }

  const focal = kind === 'furnace' ? furnaceTool(toolId, family) : kind === 'cmp' ? cmpTool(toolId, family)
    : kind === 'litho' ? lithoTool(toolId, family) : focalTool(toolId, family)
  focal.position.set(G.TOOL_X, 0, G.FRONT_Z)
  scene.add(focal)

  const vehicles = G.PARK.map((p, i) => {
    const mesh = vehicleMesh(`OHT-0${i + 1}`)
    scene.add(mesh)
    return { park: p.id, busy: [], mesh, claim: null }
  })

  const routeMat = new THREE.MeshBasicMaterial({ color: C.route, transparent: true, opacity: 0.8 })
  let routeMesh = null, routeKey = null
  const approach = dashedLine(C.route); approach.material.opacity = 0.5; scene.add(approach)
  const ghost = dashedLine(C.ghost); scene.add(ghost)

  // ---- visualisation state --------------------------------------------------
  const foups = new Map()      // lot -> mesh group
  const slots = new Map()      // waiting lot -> shelf slot id
  const slotTaken = new Set()
  const jobs = new Map()       // lot -> { arr, dep, port, end, confirmed }
  const ports = Array(nPorts).fill(null)
  const leaving = new Map()    // lot -> wall ms when it vanished from the queue
  let hot = new Set(), overflow = 0, online = true, setup = null, downText = ''
  let forming = new Set(), groups = []      // furnace: compatible groups in the queue, largest first
  let setupsMap = {}, toolSetup = null, setupWas = null   // litho: lots' needed setups; the tool's now, and before its last switch
  let lastNow = null, lastView = null, prevNow = null

  const foupFor = lot => {
    let m = foups.get(lot)
    if (!m) { m = foupMesh(); m.userData.lot = lot; scene.add(m); foups.set(lot, m) }
    return m
  }

  // Camera lock on a lot. Each frame the orbit target eases onto the lot's
  // FOUP and the camera moves with it, so the view keeps its angle and the
  // viewer can still orbit -- around the lot now, not the tool. Zooms in a
  // little while locked; "reset view" lets go and frames the bay again.
  let trackLot = null
  const trackVec = new THREE.Vector3(), trackDelta = new THREE.Vector3()
  function follow(lot) { trackLot = lot || null }
  function followed() {
    if (!trackLot) return null
    const m = foups.get(trackLot)
    return { lot: trackLot, present: !!(m && m.visible) }
  }
  function followTracked() {
    if (!trackLot) return
    const m = foups.get(trackLot)
    if (!m || !m.visible) return
    m.getWorldPosition(trackVec)
    trackVec.y += 0.5
    trackDelta.copy(trackVec).sub(controls.target).multiplyScalar(0.12)
    controls.target.add(trackDelta)
    camera.position.add(trackDelta)
    if (camera.zoom < 1.8) { camera.zoom = Math.min(1.8, camera.zoom + 0.025); camera.updateProjectionMatrix() }
  }

  // Click a FOUP to lock onto it. A drag is an orbit, not a pick, so the
  // press and release must land within a few pixels of each other.
  const ray = new THREE.Raycaster(), ndc = new THREE.Vector2()
  let onPick = null, pressAt = null
  const pickAt = (ev) => {
    const r = renderer.domElement.getBoundingClientRect()
    ndc.set(((ev.clientX - r.left) / r.width) * 2 - 1, -((ev.clientY - r.top) / r.height) * 2 + 1)
    ray.setFromCamera(ndc, camera)
    const hits = ray.intersectObjects([...foups.values()].filter(m => m.visible), true)
    for (const h of hits) {
      let o = h.object
      while (o && o.userData.lot == null) o = o.parent
      if (o && o.userData.lot != null) return o.userData.lot
    }
    return null
  }
  renderer.domElement.addEventListener('pointerdown', ev => { pressAt = { x: ev.clientX, y: ev.clientY } })
  renderer.domElement.addEventListener('pointerup', ev => {
    if (!pressAt) return
    const moved = Math.hypot(ev.clientX - pressAt.x, ev.clientY - pressAt.y)
    pressAt = null
    if (moved > 4) return
    const lot = pickAt(ev)
    if (lot != null && onPick) onPick(lot)
  })
  renderer.domElement.addEventListener('pointermove', ev => {
    if (pressAt) return
    renderer.domElement.classList.toggle('pickable', pickAt(ev) != null)
  })
  // A label is a DOM element the label renderer owns; removing its parent
  // group from the scene does not remove it (three only tells the object it
  // removed directly), so it would linger at its last screen position and stop
  // following the camera. Pull the elements out explicitly.
  const dropLabels = m => m.traverse(o => {
    if (o.isCSS2DObject && o.element && o.element.parentNode) o.element.parentNode.removeChild(o.element)
  })
  const dropFoup = lot => {
    const m = foups.get(lot)
    if (!m) return
    dropLabels(m)
    scene.remove(m)
    m.traverse(o => { if (o.geometry) o.geometry.dispose(); if (o.material) o.material.dispose() })
    foups.delete(lot)
  }
  const freePort = lot => {
    const i = ports.indexOf(null)
    return i >= 0 ? i : G.hashStr(lot) % nPorts
  }
  // A batch finishes as one; its FOUPs leave as vehicles come free.
  const planDep = (j, tEnd) => {
    const want = G.earliestFree(track, vehicles, G.portNode(j.port), tEnd, 30)
    const vi = want.vehicle < 0 ? 0 : want.vehicle
    const delay = Number.isFinite(tEnd) ? Math.max(0, want.at - tEnd) : 0
    j.dep = G.planDeparture(track, { lot: j.lot, tEnd, port: j.port, vehicle: vi, park: vehicles[vi].park, delay })
    if (Number.isFinite(tEnd)) G.reserveVehicle(vehicles[vi], tEnd + j.dep.e0, tEnd + j.dep.e5)
  }

  // Bring the visualisation's state in line with a fresh poll of the tool.
  // `nowOverride` is the replay clock; live polls carry their own.
  function reconcile(t, nowOverride) {
    const clock = { t: t.sim_t, t_at: t.sim_t_at, speed: t.speed, paused: t.paused }
    const now = nowOverride !== undefined ? nowOverride : simNow(clock, Date.now() / 1000)
    online = t.online !== false
    setup = setupLabel(t.setup)
    downText = online ? '' : `down · ${t.down_reason || 'unknown'}${t.down_s ? ` · ${fmtCountdown(t.down_s)}` : ''}`
    const running = t.running_lots || (t.running || []).map(lot => ({ lot }))
    const runningIds = new Set(running.map(r => r.lot))

    // Arrivals: a lot on the tool that we have not seen there before gets a
    // port, a vehicle, and a route from wherever it was waiting.
    for (const r of running) {
      // A lot restored from a compacted snapshot has no start time: it has
      // been on the tool since before we looked, so it is drawn there rather
      // than replayed arriving.
      const t0 = r.t ?? -Infinity
      let j = jobs.get(r.lot)
      if (!j) {
        let slot = slots.get(r.lot)
        if (slot) { slots.delete(r.lot); slotTaken.delete(slot) }
        else slot = G.assignSlot(slotTaken, r.lot) || G.stockerNode(G.hashStr(r.lot) % G.STOCKER_X.length)
        const port = freePort(r.lot)
        // Lots dispatched together (a batch) share the decision but take
        // vehicles as they come free, so the later ones wait on the shelf.
        let vi, delay = 0
        if (Number.isFinite(t0)) {
          const want = G.earliestFree(track, vehicles, slot, t0 + G.DECIDE_S, 45)
          vi = want.vehicle; delay = Math.max(0, want.at - (t0 + G.DECIDE_S))
        } else vi = G.pickVehicle(track, vehicles, slot, t0, 60)
        if (vi < 0) vi = 0
        // A litho lot that needs another layer's setup costs the changeover
        // the decision record priced it at; the tool pays it after loading.
        const chose = G.decisionForLot(t.recent_decisions, r.lot)
        const changeover = kind === 'litho' && Number.isFinite(t0) ? G.setupCostOf(chose, r.lot) : 0
        const arr = G.planArrival(track, { lot: r.lot, t0, slot, port, vehicle: vi, park: vehicles[vi].park, delay, changeover })
        if (Number.isFinite(t0)) G.reserveVehicle(vehicles[vi], t0 + arr.d1, t0 + arr.d6)
        j = { lot: r.lot, arr, port, end: r.end ?? null, dep: null, confirmed: false }
        ports[port] = r.lot
        jobs.set(r.lot, j)
        leaving.delete(r.lot)
      }
      if (r.end != null && r.end !== j.end) { j.end = r.end; j.dep = null }
      // The finish is known in advance, so the pickup vehicle can be on its
      // way before the lot is done -- the way a real AMHS pre-dispatches.
      if (j.end != null && !j.dep) planDep(j, j.end)
    }

    // Departures: a lot we had on a port that the tool no longer lists. If it
    // left about when its plan said, the sequence is anchored there; if it
    // ran long (a breakdown mid-run), it leaves now rather than snapping.
    for (const j of jobs.values()) {
      if (runningIds.has(j.lot) || j.confirmed) continue
      const out = (t.recent_out || []).find(o => o.lot === j.lot)
      let tEnd = j.end
      if (tEnd == null) tEnd = out?.day != null ? out.day * 86400 : (now ?? 0)
      if (now != null && (tEnd > now || now - tEnd > 10)) tEnd = now
      j.confirmed = true
      if (!j.dep || Math.abs(j.dep.tEnd - tEnd) > 1e-6) planDep(j, tEnd)
    }

    // The family queue on the shelves. Lots that vanish without landing on
    // this tool were taken by a sibling tool; they fade rather than teleport.
    const waiting = t.waiting || []
    for (const lot of waiting) {
      if (jobs.has(lot) || slots.has(lot)) { leaving.delete(lot); continue }
      const s = G.assignSlot(slotTaken, lot)
      if (!s) continue
      slots.set(lot, s); slotTaken.add(s)
    }
    const wset = new Set(waiting)
    for (const lot of slots.keys()) {
      if (wset.has(lot) || jobs.has(lot)) continue
      if (!leaving.has(lot)) leaving.set(lot, performance.now())
    }
    overflow = Math.max(0, (t.waiting_count ?? waiting.length) - slots.size)
    hot = new Set([...waiting, ...runningIds].filter(l => shortId(l).hot))
    // Groups count the whole family queue, not just the dozen on the shelves.
    groups = kind === 'furnace' ? G.batchGroups(t.queue_lots || waiting, t.steps) : []
    forming = new Set(groups[0] ? groups[0].lots : [])
    setupsMap = t.steps || {}
    // The tool's setup: the stats' running value, else the setup the latest
    // decision stamped (the record carries it after the switch).
    const d0 = (t.recent_decisions || [])[0]
    toolSetup = setupLabel(t.setup) || setupLabel(d0 && (d0.setup || (d0.why && d0.why.tool_setup)))
    setupWas = G.setupBefore(t.recent_decisions, 0)
    if (Number.isFinite(now)) G.pruneReservations(vehicles, now - 7200)
  }

  // Back to an empty bay: used when switching between the live feed and a
  // replay, so neither inherits the other's lots, ports or bookings.
  function reset() {
    for (const lot of [...foups.keys()]) dropFoup(lot)
    // Belt and braces: any lot label still in the layer is stale. Live labels
    // are re-attached by the renderer on the next frame, so this is safe.
    labels.domElement.querySelectorAll('.etch-lbl-lot').forEach(el => el.remove())
    jobs.clear(); slots.clear(); slotTaken.clear(); leaving.clear(); ports.fill(null)
    for (const v of vehicles) { v.busy = []; v.claim = null }
    if (routeMesh) { scene.remove(routeMesh); routeMesh.geometry.dispose(); routeMesh = null; routeKey = null }
    setDashed(ghost, null); setDashed(approach, null)
    tmp.newest = null; tmp.newestState = null; tmp.nearest = null
    lastNow = null; lastView = null; overflow = 0
  }

  // How long a replay anchored at t0 needs: the chosen lot delivered and its
  // vehicle home, or the previous lot carried out, whichever is later.
  function replayLength(t0) {
    let m = 14
    for (const j of jobs.values()) {
      if (j.arr.t0 >= t0) m = Math.max(m, j.arr.d6 + 2)
      if (j.dep && j.confirmed) m = Math.max(m, j.dep.e4 + 2)
    }
    return m
  }

  function setNeighbours(ids) {
    neighbours.forEach((m, i) => setText(m.userData.label, ids[i] || ''))
  }

  // ---- per frame ----------------------------------------------------------
  const tmp = { newest: null, newestState: null, nearest: null }

  function advance(now, wall) {
    lastNow = now
    for (const v of vehicles) v.claim = null
    let newest = null, newestState = null, activeRoute = null, approachRoute = null
    let litJob = null, litTau = Infinity
    const done = []

    for (const j of jobs.values()) {
      const tauA = now - j.arr.t0
      const a = G.arrivalAt(track, j.arr, tauA)
      let d = null
      if (j.dep) {
        let tauD = now - j.dep.tEnd
        if (!j.confirmed && tauD > 0) tauD = 0   // the sim still lists it running: hold
        d = G.departureAt(track, j.dep, tauD)
      }
      const leavingNow = d && (d.phase === 'unload' || d.phase === 'exit' || d.phase === 'gone' || d.phase === 'done')
      const m = foupFor(j.lot)
      tintLid(m, j.lot)
      if (leavingNow && !d.foup) { m.visible = false }
      else {
        m.visible = true
        const p = leavingNow ? d.foup : a.foup
        m.position.set(p.x, p.y, p.z)
        m.scale.setScalar(FOUP_SCALE)
        const pulse = 0.55 + 0.45 * Math.sin(wall * 5)
        if (leavingNow) setMat(m.userData.bodyMat, C.foupLeave, C.foupLeave, 0.35)
        else if (a.phase === 'process') setMat(m.userData.bodyMat, hot.has(j.lot) ? C.foupHot : C.foupOn, null, 0.25)
        else if (a.phase === 'select') {
          const p2 = G.decideProgress(j.arr, tauA)
          if (p2 != null && p2 >= 0.7) setMat(m.userData.bodyMat, C.foupSel, C.foupSel, pulse)
          else setMat(m.userData.bodyMat, hot.has(j.lot) ? C.foupHot : C.foupWait, null, 0)
        } else setMat(m.userData.bodyMat, C.foupSel, C.foupSel, 0.5)
        let text = shortId(j.lot).short
        if (a.phase === 'process' && !leavingNow && j.end != null && Number.isFinite(now)) text += ` · ${fmtCountdown(Math.max(0, j.end - now))}`
        setText(m.userData.lbl, text)
        setCls(m.userData.lbl, `etch-lbl etch-lbl-lot${hot.has(j.lot) ? ' etch-lbl-lot-hot' : ''}${a.phase !== 'process' || leavingNow ? ' etch-lbl-lot-live' : ''}`)
      }
      if (a.vehicle) claim(j.arr.vehicle, j.arr.t0, a, a.carrying ? 'carry' : a.phase === 'process' ? 'return' : 'assigned', j.lot)
      if (d && d.vehicle) claim(j.dep.vehicle, j.dep.tEnd + j.dep.e0, d, 'depart', j.lot)
      if (d && (d.phase === 'exit' || d.phase === 'gone' || d.phase === 'done') && ports[j.port] === j.lot) ports[j.port] = null
      if (d && d.phase === 'done') done.push(j.lot)
      // The newest decision leads; within a batch (one decision, several
      // lots) the last FOUP to land is the one the state follows.
      if (!newest || j.arr.t0 > newest.t0 || (j.arr.t0 === newest.t0 && j.arr.d5 > newest.d5)) { newest = j.arr; newestState = { tau: tauA, ...a } }
      // The lit route belongs to whichever vehicle set off most recently.
      if (tauA >= j.arr.d1 && tauA < j.arr.d4 && (!litJob || tauA - j.arr.d1 < litTau)) { litJob = j.arr; litTau = tauA - j.arr.d1 }
    }
    for (const lot of done) { jobs.delete(lot); dropFoup(lot) }
    const processing = [...jobs.values()].filter(j => !j.confirmed && now - j.arr.t0 >= j.arr.d5).length

    // Highlighted route: the chosen lot's path to the port, from the moment
    // the dispatcher commits until the FOUP is over the port. The vehicle's
    // empty approach is dashed and dimmer.
    tmp.nearest = null
    if (newest && newestState) {
      const tau = newestState.tau
      const p = G.decideProgress(newest, tau)
      if (p != null && p >= 0.88) activeRoute = { key: `${newest.lot}:r2`, route: newest.r2 }
      if (litJob) {
        activeRoute = { key: `${litJob.lot}:r2`, route: litJob.r2 }
        if (litTau < litJob.d2 - litJob.d1) approachRoute = litJob.r1
      }
      // The tempting alternative: the FOUP nearest by rail that was NOT
      // chosen, ghosted while the dispatcher is deciding.
      if (p != null && p >= 0.2 && p < 0.85) {
        let best = null
        for (const [lot, s] of slots) {
          if (leaving.has(lot)) continue
          const dm = G.railDistance(track, s, newest.port)
          if (dm != null && (!best || dm < best.m)) best = { lot, slot: s, m: dm }
        }
        const chosenM = G.railDistance(track, newest.slot, newest.port)
        if (best && chosenM != null && best.m < chosenM - 0.5) {
          tmp.nearest = best
          if (p >= 0.3) setDashed(ghost, track.route(best.slot, G.portNode(newest.port)))
        }
      }
    }
    if (!tmp.nearest) ghost.visible = false
    setDashed(approach, approachRoute)
    if (activeRoute?.key !== routeKey) {
      if (routeMesh) { scene.remove(routeMesh); routeMesh.geometry.dispose(); routeMesh = null }
      routeKey = activeRoute?.key || null
      if (activeRoute) { routeMesh = new THREE.Mesh(routeTube(activeRoute.route), routeMat); scene.add(routeMesh) }
    }
    if (routeMesh) routeMat.opacity = 0.55 + 0.3 * Math.sin(wall * 4)

    // Waiting lots on their shelves.
    for (const [lot, s] of slots) {
      const m = foupFor(lot)
      tintLid(m, lot)
      const p = G.slotPos(track, s)
      m.visible = true
      m.position.set(p.x, p.y, p.z)
      const lv = leaving.get(lot)
      if (lv != null) {
        const f = Math.min(1, (performance.now() - lv) / 600)
        m.scale.setScalar(FOUP_SCALE * (1 - f))
        m.userData.lbl.visible = f < 0.4
        if (f >= 1) { slots.delete(lot); slotTaken.delete(s); leaving.delete(lot); dropFoup(lot); continue }
      } else { m.scale.setScalar(FOUP_SCALE); m.userData.lbl.visible = true }
      const tempting = tmp.nearest && tmp.nearest.lot === lot
      // In a furnace queue the largest compatible group is the batch that is
      // forming: amber while the tool holds for it.
      const gather = forming.has(lot) && processing === 0 && !tempting
      setMat(m.userData.bodyMat, tempting ? C.foupTempt : hot.has(lot) ? C.foupHot : gather ? C.foupGather : C.foupWait, null,
             tempting ? 0.5 : gather ? 0.25 + 0.15 * Math.sin(wall * 2) : 0)
      setText(m.userData.lbl, shortId(lot).short)
      setCls(m.userData.lbl, `etch-lbl etch-lbl-lot${hot.has(lot) ? ' etch-lbl-lot-hot' : ''}${tempting ? ' etch-lbl-lot-tempt' : ''}`)
    }

    // Vehicles.
    vehicles.forEach(v => {
      const c = v.claim
      const n = track.nodes.get(v.park)
      const pos = c ? c.st.vehicle : { x: n.x, z: n.z }
      v.mesh.position.set(pos.x, G.RAIL_Y, pos.z)
      const busy = c && c.kind !== 'return'
      const led = !busy ? C.ledIdle : c.kind === 'depart' ? C.ledDepart : c.kind === 'carry' ? C.ledCarry : C.ledAssigned
      setMat(v.mesh.userData.ledMat, led, led, busy ? 1.2 : 0.5)
      const carrying = c && c.st.carrying && c.st.foup
      const { belt, grip } = v.mesh.userData
      if (carrying) {
        const top = c.st.foup.y + FOUP_H + 0.05
        const len = Math.max(0.05, (G.RAIL_Y - 0.7) - top)
        belt.visible = grip.visible = true
        belt.scale.y = len
        belt.position.y = -0.7 - len / 2
        grip.position.y = -0.7 - len
      } else { belt.visible = grip.visible = false }
      setText(v.mesh.userData.lbl, `OHT-0${vehicles.indexOf(v) + 1}${busy ? (c.kind === 'depart' ? ' · unload' : ' · ' + shortId(c.lot).short) : ''}`)
    })

    // Machine state -> lamp and floor ring.
    const state = G.toolState({ newest, tau: newestState ? newestState.tau : null, processing, online, kind, gathering: slots.size > 0 })
    const lamp = LAMP[state] || C.ledIdle
    const flick = state === 'LOT SELECTED' || state === 'BATCH SELECTED' || state === 'RESERVED' || state === 'LOADING' ? 0.6 + 0.6 * Math.sin(wall * 6)
      : state === 'WAITING FOR BATCH' ? 0.5 + 0.3 * Math.sin(wall * 1.5) : 0.9
    // Furnace tubes glow while a batch runs.
    if (focal.userData.tubeMat) focal.userData.tubeMat.emissiveIntensity = processing > 0 ? 0.55 + 0.25 * Math.sin(wall * 1.2) : 0.05
    // Litho: the band wears the setup the tool is on; during a changeover it
    // blinks between the old and the new colour and the label counts down.
    if (focal.userData.bandMat) {
      const co = newest && newestState && newest.changeover ? G.changeoverProgress(newest, newestState.tau) : null
      const newSetup = newest ? (setupsMap[newest.lot] || {}).setup || null : null
      // The record stamps the tool's setup after the switch; while the
      // changeover runs the band still shows the setup it is leaving.
      const target = newSetup || toolSetup
      const was = setupWas && setupWas !== target ? setupWas : (toolSetup && toolSetup !== target ? toolSetup : null)
      const cur = hueColor(G.setupHue(toolSetup)) || new THREE.Color(0x64748b)
      const old = hueColor(G.setupHue(was)) || new THREE.Color(0x64748b)
      const c = co != null ? (Math.sin(wall * 8) > 0 ? old : cur) : cur
      focal.userData.bandMat.color.copy(c); focal.userData.bandMat.emissive.copy(c)
      focal.userData.bandMat.emissiveIntensity = co != null ? 0.9 : 0.35
      const shown = co != null ? `${was || '…'} → ${target || '?'} · ${fmtCountdown(Math.max(0, newest.d7 - newestState.tau))} changeover`
        : toolSetup ? `setup ${toolSetup}` : 'no setup'
      setText(focal.userData.setupLbl, shown)
    }
    setMat(focal.userData.lampMat, lamp, lamp, flick)
    focal.userData.ringMat.color.setHex(lamp)
    focal.userData.ringMat.opacity = state === 'IDLE' ? 0.25 : 0.5
    setText(focal.userData.sub, online
      ? `${family} · ${state.toLowerCase()}${setup ? ` · setup ${setup}` : ''}`
      : `${family} · ${downText}`)
    // CMP platens turn while a lot is on the tool, at the fab clock's pace:
    // a blur at 20x, a slow turn at 1x, still when paused.
    if (focal.userData.spin.length && processing > 0 && Number.isFinite(now) && Number.isFinite(prevNow)) {
      const dt = Math.max(0, Math.min(now - prevNow, 5))
      for (const m of focal.userData.spin) m.rotation.y += dt * 1.8
      if (focal.userData.armGroup) focal.userData.armGroup.rotation.y = 0.12 * Math.sin(now * 0.7)
    }
    prevNow = Number.isFinite(now) ? now : null

    tmp.newest = newest; tmp.newestState = newestState
    lastView = { state, processing }
  }

  // Lid colour = the setup the lot needs, so a queue of mixed layers reads at
  // a glance; lots with no setup keep the plain lid.
  function tintLid(m, lot) {
    if (kind !== 'litho' || !m.userData.lidMat) return
    const hue = G.setupHue((setupsMap[lot] || {}).setup)
    const c = hueColor(hue, 0.5)
    if (c) m.userData.lidMat.color.copy(c); else m.userData.lidMat.color.setHex(C.foupLid)
  }

  function claim(vi, startKey, st, kind, lot) {
    const v = vehicles[vi]
    if (!v) return
    if (v.claim && v.claim.startKey > startKey) return
    v.claim = { startKey, st, kind, lot }
  }

  function frame(now) {
    followTracked()
    controls.update()
    advance(now == null ? Infinity : now, performance.now() / 1000)
    renderer.render(scene, camera)
    labels.render(scene, camera)
  }

  function resize() {
    const w = host.clientWidth, h = host.clientHeight
    if (!w || !h) return
    const aspect = w / h
    camera.left = -VIEW_W / 2; camera.right = VIEW_W / 2
    camera.top = VIEW_W / aspect / 2; camera.bottom = -VIEW_W / aspect / 2
    camera.updateProjectionMatrix()
    renderer.setSize(w, h)
    labels.setSize(w, h)
  }

  // What the overlays need, read at a few Hz by React.
  function view(t) {
    const newest = tmp.newest, st = tmp.newestState
    const j = newest ? jobs.get(newest.lot) : null
    return {
      now: lastNow,
      state: lastView?.state || 'IDLE',
      processing: [...jobs.values()]
        .filter(x => !x.confirmed && !(Number.isFinite(lastNow) && lastNow - x.arr.t0 < x.arr.d5))
        .map(x => ({ lot: x.lot, port: x.port, end: x.end, left: x.end != null && Number.isFinite(lastNow) ? Math.max(0, x.end - lastNow) : null })),
      newest: newest ? {
        lot: newest.lot, tau: st.tau, phase: st.phase, carrying: !!st.carrying, progress: G.decideProgress(newest, st.tau),
        vehicle: `OHT-0${newest.vehicle + 1}`, port: newest.port + 1, slot: newest.slot,
        railM: G.railDistance(track, newest.slot, newest.port), delivered: st.phase === 'process',
        leaving: !!(j && j.confirmed),
      } : null,
      nearest: tmp.nearest ? { lot: tmp.nearest.lot, railM: tmp.nearest.m } : null,
      railOf: lot => {
        if (newest && lot === newest.lot) return G.railDistance(track, newest.slot, newest.port)
        const s = slots.get(lot)
        return s ? G.railDistance(track, s, newest ? newest.port : 1) : null
      },
      shelved: slots.size, overflow,
      kind, groups, forming: [...forming],
      toolSetup, setupWas, setupOf: lot => (setupsMap[lot] || {}).setup || null,
      changeover: newest && st && newest.changeover ? { s: newest.changeover, progress: G.changeoverProgress(newest, st.tau), to: (setupsMap[newest.lot] || {}).setup || null } : null,
      batchSize: newest && Number.isFinite(newest.t0) ? [...jobs.values()].filter(x => x.arr.t0 === newest.t0).length : 1,
    }
  }

  function dispose() {
    controls.dispose()
    scene.traverse(o => {
      if (o.geometry) o.geometry.dispose()
      if (o.material) { Array.isArray(o.material) ? o.material.forEach(m => m.dispose()) : o.material.dispose() }
    })
    renderer.dispose()
    host.replaceChildren()
  }

  resize()
  return {
    frame, reconcile, reset, replayLength, resize, dispose, setNeighbours, view,
    track: follow, tracked: followed, setOnPick: fn => { onPick = fn },
    resetView: () => { trackLot = null; controls.reset() },
  }
}

// Real neighbours from the synthetic floor plan: the other tools in this
// tool's cell, same family first, so the subdued context is at least honest
// about who shares the bay.
function neighbourIds(layout, t) {
  const n = NEIGHBOURS_FRONT.length + NEIGHBOURS_BACK.length
  if (!layout || !layout.assign || !layout.assign[t.id]) return Array(n).fill('')
  const [bay, seg] = layout.assign[t.id]
  const cell = (layout.cell_tools || {})[`${bay},${seg}`] || []
  const fam = t.group || ''
  const others = cell.filter(id => id !== t.id)
    .sort((a, b) => (b.startsWith(fam) - a.startsWith(fam)) || a.localeCompare(b))
  return Array.from({ length: n }, (_, i) => others[i] || '')
}

// ---------------------------------------------------------------------------
// Overlays
// ---------------------------------------------------------------------------

const fmtM = m => m == null ? '—' : `${Math.round(m)} m`
const decisionKey = d => d ? `${d.day}|${d.ts}|${d.tool || ''}` : ''
export const canReplay = d => !!(d && d.why && d.why.chosen && d.why.chosen.length)

function DispatchPanel({ t, view, decision, replaying, kind }) {
  const d = decision
  // The API decodes the feed's base64 rationale; an API older than that
  // decode hands the blob through as a string, so decode here as a fallback.
  let why = d?.why
  if (typeof why === 'string') {
    try { why = JSON.parse(atob(why.replace(/-/g, '+').replace(/_/g, '/'))) } catch { why = null }
  }
  const newest = view?.newest
  const p = newest?.progress
  const rule = String(why?.rule || d?.src || '').replace(/^rule:/, '') || '—'
  let rows, keys = why?.keys || [], note = null
  if (why) {
    rows = [...(why.chosen || []).map(c => ({ ...c, win: true })), ...(why.alternatives || []).slice(0, 5)]
      .filter((r, i, arr) => arr.findIndex(x => x.lot === r.lot) === i)
  } else {
    const started = (t.recent_events || []).find(e => e.type === 'LOT_STARTED' && newest && e.lot === newest.lot)
    rows = [
      ...(newest ? [{ lot: newest.lot, win: true, prio: started?.prio, step: started?.recipe }] : []),
      ...(t.waiting || []).filter(l => !newest || l !== newest.lot).slice(0, 5).map(lot => ({ lot })),
    ]
    note = 'This feed did not record the rationale (it predates the explainer); showing the family queue, oldest first.'
  }
  // Scan-and-lock: while the dispatcher is "deciding", rows light in turn,
  // then the winner locks and the assignment appears.
  const scanIdx = p != null && p < 0.7 ? Math.floor(((p - 0.1) / 0.6) * rows.length) : -1
  const locked = p == null || p >= 0.7
  const assigned = newest && (p == null || p >= 0.88)
  const inFlight = newest && !newest.delivered
  const nextOf = lot => (t.next || {})[lot]
  const stepOf = lot => (t.steps || {})[lot]
  const furnace = kind === 'furnace'
  const chosenLots = why ? (why.chosen || []).map(c => c.lot) : (newest ? [newest.lot] : [])
  const batchStep = chosenLots.length ? (stepOf(chosenLots[0]) || {}) : {}
  const groups = view?.groups || []
  const fmtStep = st => String(st || '—').replace(/_/g, ' ')
  const litho = kind === 'litho'
  const showSetup = litho || rows.some(r => r.setup_match === false) || (t.setup && t.setup !== '-')
  const swatch = su => { const h = G.setupHue(su); return h == null ? null : <i className="etch-sw" style={{ background: `hsl(${h} 65% 55%)` }} /> }
  const win = rows.find(r => r.win)
  const setupCost = why && win ? G.setupCostOf(d, win.lot) : 0
  // The rules rank setup cost ahead of queue age: say so when a lot that
  // waited longer lost to one already on the tool's layer.
  const passedOver = litho && win && win.setup_match ? rows.find(r => !r.win && r.setup_match === false && r.wait_s != null && win.wait_s != null && r.wait_s > win.wait_s) : null
  const showNext = rows.some(r => nextOf(r.lot))
  const pm = t.pm_int ? { used: Math.max(0, t.pm_int - (t.pm_left ?? t.pm_int)), int: t.pm_int } : null
  const pmFrac = pm ? Math.min(1, pm.used / pm.int) : 0
  const phaseText = !newest ? '' : newest.phase === 'select' ? 'reserving'
    : newest.phase === 'reserve' ? (newest.carrying ? 'hoisting from the shelf' : 'vehicle to pickup')
    : newest.phase === 'transit' ? 'in transit' : newest.phase === 'changeover' ? 'on the port · setup change' : 'lowering onto the port'
  return (
    <div className="etch-panel">
      <div className="etch-panel-head">
        <strong>DISPATCHER</strong>
        <span className="etch-panel-rule" title="the rule or slate source that made this decision">{rule}</span>
      </div>
      <div className="etch-panel-sub">
        {d ? <>{replaying ? 'replaying: ' : ''}tool freed at day {Number(d.day).toFixed(3)} · {d.qbefore ?? '—'} candidates</> : 'no decision recorded for this tool yet'}
      </div>
      {pm && (
        <div className={`etch-pm${pmFrac > 0.9 ? ' red' : pmFrac > 0.7 ? ' amber' : ''}`}
             title="SMT2020 schedules this tool's maintenance by pieces processed: the tool goes down for PM after this many wafers. The nearest thing the testbed has to consumable (pad) wear.">
          <span className="etch-pm-lbl">pad life</span>
          <span className="etch-pm-bar"><i style={{ width: `${Math.round(pmFrac * 100)}%` }} /></span>
          <span className="etch-pm-txt">{Math.round(pm.used).toLocaleString()} of {Math.round(pm.int).toLocaleString()} wafers since PM</span>
        </div>
      )}
      {t.online === false && (
        <div className="etch-down">tool down · {t.down_reason || 'unknown'}{t.down_s ? ` · ${fmtCountdown(t.down_s)} outage` : ''} — the family's other tools take the queue</div>
      )}
      {litho && (
        <div className="etch-batch">
          {view?.changeover && view.changeover.progress != null
            ? <>{swatch(view.setupWas)}<b>{view.setupWas || '…'}</b><span className="etch-co"> → {swatch(view.changeover.to || view.toolSetup)}{view.changeover.to || view.toolSetup} · {fmtCountdown(Math.max(0, view.changeover.s * (1 - view.changeover.progress)))} of changeover left</span></>
            : <>{swatch(view?.toolSetup)}tool on <b>{view?.toolSetup || 'no setup'}</b>
                {setupCost > 0 && win ? <span className="etch-dim"> · {newest && !newest.delivered ? 'switches' : 'switched'} for {win.lot} ({win.setup}) · {fmtCountdown(setupCost)} changeover</span>
                  : win && win.setup ? <span className="etch-dim"> · winner already on this layer, no changeover</span> : null}</>}
        </div>
      )}
      {furnace && chosenLots.length > 0 && (
        <div className="etch-batch">
          <b>batch of {chosenLots.length}</b> · {G.partOf(chosenLots[0]) || '?'} · {fmtStep(batchStep.step || (why?.chosen?.[0]?.step))}
          {batchStep.bmin || batchStep.bmax ? <span className="etch-dim"> · min {batchStep.bmin ?? '?'} · max {batchStep.bmax ?? '?'} lots</span> : null}
          <span className="etch-dim"> · same product, same step</span>
        </div>
      )}
      {furnace && groups.length > 0 && (
        <div className="etch-groups" title="lots waiting for this family, grouped by product and step: only lots of one group can share a batch">
          {groups.slice(0, 3).map((g, i) => (
            <span key={`${g.part}|${g.step}`} className={`etch-group${i === 0 && view?.state === 'WAITING FOR BATCH' ? ' forming' : ''}`}>
              {g.part} · {fmtStep(g.step)} <b>×{g.lots.length}</b>{g.bmin ? <span className="etch-dim"> / min {g.bmin}</span> : null}
              {i === 0 && view?.state === 'WAITING FOR BATCH' && <span className="etch-forming"> forming</span>}
            </span>
          ))}
          {groups.length > 3 && <span className="etch-dim">+{groups.length - 3} more groups</span>}
        </div>
      )}
      <table className="etch-tbl">
        <thead><tr><th>lot</th>{furnace && <th title="the step each candidate is at: a batch takes one step only">step</th>}<th>prio</th><th>waited</th><th>slack</th>{showSetup && <th>setup</th>}
          {showNext && <th title="the family the lot's next route step needs, and how many lots are queued there now">next ↓</th>}
          <th title="rail distance from where the FOUP sits to the load port (visualisation layout)">rail</th></tr></thead>
        <tbody>
          {rows.map((r, i) => {
            const cls = [r.win && locked ? 'win' : '', i === scanIdx ? 'scan' : '', r.win && !locked ? 'cand' : ''].join(' ')
            return (
              <tr key={r.lot} className={cls}>
                <td>{r.win && locked ? '▶ ' : ''}{shortId(r.lot).short}{shortId(r.lot).hot ? <span className="etch-hot"> hot</span> : ''}</td>
                {furnace && <td className={r.step && batchStep.step && r.step !== batchStep.step ? 'other' : ''}>{fmtStep(r.step || stepOf(r.lot)?.step)}</td>}
                <td>{r.prio ?? '—'}</td>
                <td>{r.wait_s != null ? G.fmtDur(r.wait_s) : '—'}</td>
                <td className={r.slack_s != null && r.slack_s < 0 ? 'neg' : ''}>{r.slack_s != null ? G.fmtDur(r.slack_s) : '—'}</td>
                {showSetup && (litho
                  ? <td className={r.setup_match === false ? 'other' : ''}>{swatch(r.setup)}{r.setup || (view?.setupOf ? view.setupOf(r.lot) : null) || '—'}{r.setup_match === false ? <span className="etch-q"> change</span> : ''}</td>
                  : <td>{r.setup_match == null ? '—' : r.setup_match ? 'same' : 'change'}</td>)}
                {showNext && (() => { const nx = nextOf(r.lot); return (
                  <td className={nx && nx.waiting >= 20 ? 'busy' : ''} title={nx ? `next step ${nx.step} · ${nx.fam} · ${nx.waiting} waiting there now` : 'next step not known to the mirror'}>
                    {nx ? <>{String(nx.fam).replace(/_/g, ' ')} <span className="etch-q">q{nx.waiting}</span></> : '—'}
                  </td>) })()}
                <td>{fmtM(view?.railOf ? view.railOf(r.lot) : null)}</td>
              </tr>
            )
          })}
          {rows.length === 0 && <tr><td colSpan={8} className="etch-dim">nothing waiting</td></tr>}
        </tbody>
      </table>
      {keys.length > 0 && <div className="etch-panel-keys">ordered by {keys.join(' › ')}, smallest first</div>}
      {note && <div className="etch-panel-note">{note}</div>}
      {newest && (
        <div className={`etch-assign${assigned ? ' on' : ''}`}>
          <span className="etch-assign-lot">{newest.lot}{view?.batchSize > 1 ? <span className="etch-dim"> +{view.batchSize - 1}</span> : null}</span>
          <span className="etch-arrow">→</span>
          <span className="etch-assign-oht">{newest.vehicle}</span>
          <span className="etch-arrow">→</span>
          <span className="etch-assign-tool">{t.id} · LP{newest.port}</span>
          <div className="etch-assign-sub">
            {inFlight ? `${phaseText} · ${fmtM(newest.railM)} by rail`
              : newest.leaving ? 'finished · leaving the bay' : `on LP${newest.port} · processing`}
          </div>
        </div>
      )}
      {passedOver && (
        <div className="etch-nearest etch-passed">
          <b>{shortId(passedOver.lot).short}</b> waited longer ({G.fmtDur(passedOver.wait_s)}) but needs {passedOver.setup}: a changeover. The rule ranks setup cost ahead of queue age.
        </div>
      )}
      {view?.nearest && (
        <div className="etch-nearest">
          nearest FOUP was <b>{shortId(view.nearest.lot).short}</b> at {fmtM(view.nearest.railM)} — not chosen: the dispatcher ranks the lot, not the distance
        </div>
      )}
    </div>
  )
}

// Playback speed is a header setting; the twin needs 1x, so the chip offers
// it directly. Same endpoint the header's menu posts to.
const setLive1x = () => fetch('/api/sim/control', {
  method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ speed: 1, paused: false }),
}).catch(() => {})

function ClockChip({ t, view, replay, onReset }) {
  const speed = t.speed, paused = t.paused
  const day = view?.now != null && Number.isFinite(view.now) ? view.now / 86400 : null
  if (replay) {
    const el = Math.max(0, replay.elapsed)
    return (
      <div className="etch-clock etch-clock-replay">
        <div className="etch-clock-row">
          <span className="etch-clock-day">replay t+{el.toFixed(0)} s</span>
          <span className="etch-clock-speed play">{replay.finished ? 'finished' : '1x'}</span>
          <button type="button" className="etch-reset" onClick={onReset} title="reset the camera">⟲ view</button>
        </div>
        <div className="etch-clock-hint">
          {replay.length ? `${el.toFixed(0)} of ${replay.length.toFixed(0)} s, ` : ''}one fab second per second, whatever the feed is doing · decision at day {Number(replay.day).toFixed(3)}
        </div>
      </div>
    )
  }
  let hint
  if (paused) hint = 'fab clock paused · nothing moves'
  else if (speed != null && speed > 1) hint = 'vehicles move in fab time · at 1x this is the bay in real time'
  else if (speed === 1) hint = 'digital twin · one fab second per second'
  else hint = 'no fab clock on the wire · resting positions only'
  return (
    <div className="etch-clock">
      <div className="etch-clock-row">
        <span className="etch-clock-day">{day == null ? 'day —' : `day ${day.toFixed(3)}`}</span>
        <span className={`etch-clock-speed${paused ? ' paused' : speed === 1 ? ' real' : ''}`}>{paused ? 'paused' : speed != null ? `${speed}x` : '—'}</span>
        {speed != null && (paused || speed !== 1) && (
          <button type="button" className="etch-reset etch-1x" onClick={setLive1x} title="run the fab at 1x: the scene becomes a live twin">
            {paused ? '▶ resume at 1x' : 'set 1x'}
          </button>
        )}
        <button type="button" className="etch-reset" onClick={onReset} title="reset the camera">⟲ view</button>
      </div>
      <div className="etch-clock-hint">{hint}</div>
    </div>
  )
}

function ModeBar({ mode, latest, replay, onLive, onPlayback, onReplay }) {
  return (
    <div className="etch-modes" role="tablist" aria-label="scene mode">
      <button type="button" role="tab" aria-selected={mode === 'live'} className={mode === 'live' ? 'on' : ''}
              onClick={onLive} title="follow the live feed: at 1x playback this is a digital twin of the bay">● live twin</button>
      <button type="button" role="tab" aria-selected={mode === 'playback'} className={mode === 'playback' ? 'on play' : ''}
              disabled={mode !== 'playback' && !latest} onClick={() => mode === 'playback' ? onReplay() : onPlayback(latest)}
              title={latest ? 'replay a recorded decision at 1x; pick one under Recent decisions' : 'no decision with a recorded rationale yet'}>
        ▶ playback
      </button>
      {mode === 'playback' && replay && (
        <>
          <span className="etch-modes-what">day {Number(replay.day).toFixed(3)} · {replay.lot}</span>
          <button type="button" className="etch-modes-act" onClick={onReplay} title="play it again">↻</button>
          <button type="button" className="etch-modes-act" onClick={onLive} title="back to the live feed">✕</button>
        </>
      )}
    </div>
  )
}

function TrackChip({ tracked, onClear }) {
  if (!tracked) return null
  return (
    <div className={`etch-track${tracked.present ? '' : ' away'}`}>
      <span>◎ {tracked.present ? 'tracking' : 'waiting for'}</span>
      <code>{tracked.lot}</code>
      {!tracked.present && <span>· not in this scene</span>}
      <button type="button" onClick={onClear} title="stop following this lot">✕</button>
    </div>
  )
}

function Legend({ view }) {
  return (
    <div className="etch-legend">
      <span><i style={{ background: '#9aa8bf' }} />waiting</span>
      <span><i style={{ background: '#ef4444' }} />hot lot</span>
      <span><i style={{ background: '#22d3ee' }} />selected · in transit</span>
      <span><i style={{ background: '#22c55e' }} />on tool</span>
      <span><i style={{ background: '#a78bfa' }} />leaving</span>
      <span><i style={{ background: '#f87171', borderRadius: 0, height: 2, marginTop: 4 }} />nearest, not chosen</span>
      {view && (view.overflow > 0) && <span className="etch-dim">+{view.overflow} more waiting than the shelves show</span>}
      <span className="etch-dim">drag to orbit · wheel to zoom · click a FOUP to track it</span>
    </div>
  )
}

function StateStrip({ view, t, mode, hasReplayable, kind }) {
  const state = view?.state || 'IDLE'
  const base = G.statesFor(kind)
  const states = state === 'DOWN' ? [...base, 'DOWN'] : base
  const g0 = view?.groups?.[0]
  return (
    <div className="etch-strip">
      <div className="etch-states">
        <code className="etch-strip-id">{t.id}:</code>
        {states.map((s, i) => (
          <span key={s} className="etch-state-wrap">
            {i > 0 && <span className="etch-state-arrow">→</span>}
            <span className={`etch-state${s === state ? ' on' : ''}${s === 'DOWN' ? ' down' : ''}`}>{s}</span>
          </span>
        ))}
        {mode === 'playback' && <span className="etch-strip-mode">replay</span>}
      </div>
      <div className="etch-strip-sub">
        {state === 'WAITING FOR BATCH' && g0 && (
          <span className="etch-gathering">holding for a batch: {g0.part} · {String(g0.step).replace(/_/g, ' ')} · {g0.lots.length}{g0.bmin ? ` of min ${g0.bmin}` : ''} lots gathered</span>
        )}
        {view?.processing?.length
          ? view.processing.map(p => (
            <span key={p.lot}><code>{p.lot}</code> on LP{p.port + 1}{p.left != null ? ` · ${fmtCountdown(p.left)} left` : ''}</span>
          ))
          : <span className="etch-dim">nothing on the tool</span>}
        {mode === 'live' && hasReplayable && (
          <span className="etch-dim">▶ watch on a recent decision below replays it here at 1x.</span>
        )}
        <span className="etch-dim">
          AMHS, ports and shelves are a visualisation of the decision: SMT2020 models transport as Delay steps, and the layout is synthetic (see Floor).
        </span>
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// The component. Two modes:
//   live      the scene follows the tool's polls on the fab clock; at 1x it
//             is a digital twin of the bay, at 20x a flick, paused still.
//   playback  a recorded decision (`playback` prop, picked under Recent
//             decisions) is replayed on its own clock at one fab second per
//             wall second, whatever the feed is doing: the previous lot
//             leaves, the candidates sit on the shelves, the dispatcher
//             chooses, the vehicle delivers. Amber border while it runs.
// ---------------------------------------------------------------------------
export default function EtchScene({ t, playback = null, onPlayback, track = null, onTrack }) {
  const hostRef = useRef(null)
  const worldRef = useRef(null)
  const tRef = useRef(t)
  const playRef = useRef(null)   // { decision, scenario, startWall, phase2, end, finished }
  const [layout, setLayout] = useState(null)
  const [, tick] = useState(0)
  useEffect(() => { tRef.current = t }, [t])

  useEffect(() => {
    let live = true
    fetch('/api/layout').then(r => r.json()).then(j => live && !j.error && setLayout(j)).catch(() => {})
    return () => { live = false }
  }, [])

  const startReplay = (world, decision) => {
    const tt = tRef.current
    const decisions = tt.recent_decisions || []
    const idx = decisions.findIndex(d => decisionKey(d) === decisionKey(decision))
    const prevLot = idx >= 0 ? G.prevLotOf(decisions, idx) : null
    const t0 = Number(decision.day) * 86400
    const scenario = G.replayScenario({ tool: tt, decision, prevLot, t0 })
    world.reset()
    world.reconcile(scenario.before, scenario.start)
    playRef.current = { decision, scenario, startWall: performance.now(), phase2: false, end: null, finished: false }
  }
  const replayNow = (world) => {
    const p = playRef.current
    let now = p.scenario.start + (performance.now() - p.startWall) / 1000
    if (!p.phase2 && now >= p.scenario.t0) {
      world.reconcile(p.scenario.after, now)
      p.phase2 = true
      p.end = p.scenario.t0 + world.replayLength(p.scenario.t0)
    }
    if (p.end != null && now >= p.end) { now = p.end; p.finished = true }
    return now
  }

  useEffect(() => {
    const host = hostRef.current
    if (!host) return undefined
    let world
    try { world = createWorld(host, tRef.current.id, tRef.current.group || '', G.sceneKind(tRef.current.group) || 'etch') }
    catch (e) { console.error('EtchScene: WebGL unavailable', e); host.textContent = 'WebGL is not available in this browser.'; return undefined }
    worldRef.current = world
    world.setOnPick(lot => onTrack && onTrack(lot))
    if (playback && canReplay(playback)) startReplay(world, playback)
    else world.reconcile(tRef.current)
    world.track(track)
    const clock = () => {
      if (playRef.current) return replayNow(world)
      const tt = tRef.current
      return simNow(tt ? { t: tt.sim_t, t_at: tt.sim_t_at, speed: tt.speed, paused: tt.paused } : null, Date.now() / 1000)
    }
    let raf = 0
    const loop = () => { raf = requestAnimationFrame(loop); if (!document.hidden) world.frame(clock()) }
    loop()
    const ro = new ResizeObserver(() => world.resize())
    ro.observe(host)
    const iv = setInterval(() => tick(n => n + 1), 200)
    return () => {
      cancelAnimationFrame(raf); ro.disconnect(); clearInterval(iv)
      world.dispose(); worldRef.current = null; playRef.current = null
    }
  }, [])

  // Live polls only steer the scene in live mode; a replay owns the bay.
  useEffect(() => { if (!playRef.current) worldRef.current?.reconcile(t) }, [t])
  useEffect(() => { worldRef.current?.setNeighbours(neighbourIds(layout, t)) }, [layout, t.id, t.group])

  useEffect(() => { worldRef.current?.track(track) }, [track])

  // Entering, switching or leaving a replay.
  useEffect(() => {
    const world = worldRef.current
    if (!world) return
    if (playback && canReplay(playback)) {
      if (decisionKey(playRef.current?.decision) !== decisionKey(playback)) startReplay(world, playback)
    } else if (playRef.current) {
      playRef.current = null
      world.reset()
      world.reconcile(tRef.current)
    }
  }, [playback])

  const world = worldRef.current
  const view = world ? world.view(t) : null
  const p = playRef.current
  const mode = p ? 'playback' : 'live'
  const decisions = t.recent_decisions || []
  const latest = decisions.find(canReplay) || null
  const decision = p ? p.decision : decisions[0]
  const replay = p ? {
    day: p.decision.day, lot: (p.decision.why?.chosen || []).map(c => c.lot).join(', '),
    elapsed: (view?.now ?? p.scenario.start) - p.scenario.start, length: p.end != null ? p.end - p.scenario.start : null,
    finished: p.finished,
  } : null
  const kind = G.sceneKind(t.group) || 'etch'
  const twin = mode === 'live' && t.speed === 1 && !t.paused
  const stageCls = `etch-stage mode-${mode}${twin ? ' mode-twin' : ''}`
  const goLive = () => onPlayback && onPlayback(null)
  const goPlayback = d => { if (onPlayback && d) onPlayback(d) }
  const again = () => { if (world && p) startReplay(world, p.decision) }
  const resetView = () => { if (onTrack) onTrack(null); world?.resetView() }
  const tracked = world ? world.tracked() : null

  return (
    <div className="etch">
      <div className={stageCls}>
        <div className="etch-canvas" ref={hostRef} />
        <div className="etch-overlays">
          <ClockChip t={t} view={view} replay={replay} onReset={resetView} />
          <TrackChip tracked={tracked} onClear={() => { if (onTrack) onTrack(null) }} />
          <ModeBar mode={mode} latest={latest} replay={replay} onLive={goLive} onPlayback={goPlayback} onReplay={again} />
          <DispatchPanel t={t} view={view} decision={decision} replaying={mode === 'playback'} kind={kind} />
          <Legend view={view} />
        </div>
      </div>
      <StateStrip view={view} t={t} mode={mode} hasReplayable={!!latest} kind={kind} />
    </div>
  )
}
