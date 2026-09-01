import assert from 'node:assert/strict'
import test from 'node:test'
import { W, H, M, WINDOW, FUTURE, view, points, areaPath } from './avail_geom.js'

const seq = (n, f) => Array.from({ length: n }, (_, i) => f(i))

test('the axis windows to WINDOW below the roster, not to zero', () => {
  const v = view(seq(5, i => i), seq(5, () => 1300), seq(5, () => 1313), 1313)
  assert.equal(v.yMax, 1313)
  assert.equal(v.yMin, 1313 - WINDOW)
  // The whole point: 13 tools down has to be visibly off the reference line.
  // Zero-based this was 1% of the plot; windowed it is 13%.
  const gap = (v.y(1300) - v.y(1313)) / v.ih
  assert.ok(gap > 0.1, `13 down rendered as ${(100 * gap).toFixed(1)}% of height`)
})

test('x follows the clock, so a pause has no width and fast playback does', () => {
  // Five samples a wall-second apart, but the fab clock stood still between
  // the 2nd and 4th and then leapt: the flat stretch must collapse.
  const simT = [0, 100, 100, 100, 500]
  const v = view(simT, seq(5, () => 10), seq(5, () => 10), 10)
  assert.equal(v.x(1), v.x(2))
  assert.equal(v.x(2), v.x(3))
  assert.ok(v.x(4) - v.x(3) > v.x(1) - v.x(0))
  assert.ok(Math.abs(v.x(4) - v.nowX) < 1e-9)
  // Identical stamps fall back to even spacing rather than dividing by zero.
  const flat = view([7, 7, 7], seq(3, () => 10), seq(3, () => 10), 10)
  assert.ok(flat.x(1) > flat.x(0) && flat.x(2) > flat.x(1))
})

test('a full roster sits on the reference line at the top', () => {
  const v = view(seq(3, i => i), seq(3, () => 1313), seq(3, () => 1313), 1313)
  assert.equal(v.y(1313), M.t)
})

test('an outage deeper than the window pushes the floor down, not off-plot', () => {
  // 300 down is well past WINDOW. Clamping instead of expanding would draw a
  // flat line along the bottom and hide the very event worth seeing.
  const online = [1313, 1013]
  const v = view([0, 1], online, [1313, 1313], 1313)
  assert.ok(v.yMin <= 1013, `floor ${v.yMin} must reach the deepest point`)
  assert.ok(v.y(1013) <= H - M.b + 1e-9, 'deepest point stays on the plot')
  assert.ok(v.y(1013) > v.y(1313), 'and is below the top')
})

test('the floor never goes negative on a small fab', () => {
  const v = view([0, 1], [8, 6], [10, 10], 10)
  assert.equal(v.yMin, 0, 'a 10-tool fab windows to zero, not to -90')
  assert.ok(v.y(0) <= H - M.b + 1e-9)
})

test('values outside the window clamp onto the plot', () => {
  const v = view([0, 1], [1300, 1310], [1313, 1313], 1313)
  // Nothing may render above the top or below the bottom of the plot box.
  for (const val of [-50, 0, 99999]) {
    assert.ok(v.y(val) >= M.t - 1e-9 && v.y(val) <= H - M.b + 1e-9, `y(${val})`)
  }
})

test('x spans the measured region and the newest sample pins to the now rule', () => {
  const v3 = view([0, 1, 2], [1, 1, 1], [1, 1, 1], 1)
  assert.equal(v3.x(0), M.l)
  assert.equal(v3.x(2), v3.nowX)
  const v1 = view([0], [1], [1], 1)
  assert.equal(v1.x(0), v1.nowX, 'newest point lives on the rule')
})

test('the rule leaves known-empty space between the last sample and the frame', () => {
  const v = view([0, 1, 2], [1, 1, 1], [1, 1, 1], 1)
  const future = (M.l + v.iw) - v.nowX
  assert.ok(future > 0, 'a rule on the frame edge is just a border')
  assert.ok(Math.abs(future / v.iw - FUTURE) < 1e-9)
  assert.ok(v.x(2) <= v.nowX + 1e-9, 'no measurement is drawn into the future')
})

test('a growing roster is tracked rather than read as an outage', () => {
  // Tools announce themselves over the first samples, so online tracks total.
  // Each point must sit on its own roster line, not below a fixed cap.
  const v = view(seq(3, i => i), [1200, 1280, 1313], [1200, 1280, 1313], 1313)
  assert.equal(v.yMax, 1313)
  for (const [on, tot] of [[1200, 1200], [1280, 1280], [1313, 1313]]) {
    assert.equal(v.y(on), v.y(tot), 'online sits exactly on the roster line')
  }
})

test('the area closes on the floor, not on zero', () => {
  const v = view([0, 1, 2], [1310, 1300, 1305], [1313, 1313, 1313], 1313)
  const base = v.y(v.yMin).toFixed(1)
  const d = areaPath(v, [1310, 1300, 1305])
  assert.ok(d.startsWith(`M${M.l.toFixed(1)},${base}`), `opens on the floor: ${d.slice(0, 30)}`)
  assert.ok(d.endsWith(`,${base} Z`), 'closes on the floor')
  assert.ok(Number(base) <= H - M.b + 1e-9, 'floor is inside the plot')
  assert.equal(areaPath(view([0], [5], [5], 5), [5]), null, 'needs two points')
})

test('empty input yields no view rather than NaN geometry', () => {
  assert.equal(view([], [], [], 0), null)
})

test('points are finite and on-plot for a realistic LVHM series', () => {
  const total = seq(60, () => 1313)
  const online = seq(60, i => 1313 - (i > 20 && i < 40 ? 300 : 27))
  const v = view(seq(60, i => i), online, total, 1313)
  for (const p of points(v, online).split(' ')) {
    const [x, y] = p.split(',').map(Number)
    assert.ok(Number.isFinite(x) && Number.isFinite(y), p)
    assert.ok(y >= M.t - 1e-9 && y <= H - M.b + 1e-9, p)
  }
})
