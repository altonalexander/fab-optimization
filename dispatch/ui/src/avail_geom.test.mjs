import assert from 'node:assert/strict'
import test from 'node:test'
import { W, H, M, view, points, areaPath } from './avail_geom.js'

const seq = (n, f) => Array.from({ length: n }, (_, i) => f(i))

test('a full roster draws flat on the reference line', () => {
  const v = view(seq(5, i => i), seq(5, () => 100), seq(5, () => 100), 100)
  assert.equal(v.yMax, 100)
  // Every point sits at the top of the plot, which is where the dashed total
  // line is -- so "all online" reads as the series touching the cap.
  for (let i = 0; i < 5; i++) assert.equal(v.y(100), M.t)
})

test('the axis is zero-based, so a small dip stays small', () => {
  const v = view(seq(2, i => i), [100, 98], [100, 100], 100)
  const drop = v.y(98) - v.y(100)
  // 2% of the plot height, not a cliff. A min-based axis would put this at
  // 100% of the height.
  assert.ok(Math.abs(drop / v.ih - 0.02) < 1e-9, `drop was ${drop / v.ih}`)
})

test('a growing roster is tracked, not flattened to the latest total', () => {
  // Tools announce themselves over the first samples: online tracks total, so
  // the chart must not read the warm-up as a 40% outage.
  const v = view(seq(3, i => i), [60, 80, 100], [60, 80, 100], 100)
  assert.equal(v.yMax, 100)
  assert.ok(v.y(60) > v.y(100), 'early samples sit lower on the plot')
})

test('x spans the plot and a single sample pins to the right edge', () => {
  const v3 = view([0, 1, 2], [1, 1, 1], [1, 1, 1], 1)
  assert.equal(v3.x(0), M.l)
  assert.equal(v3.x(2), W - M.r)
  const v1 = view([0], [1], [1], 1)
  assert.equal(v1.x(0), W - M.r, 'newest point lives at the right edge')
})

test('y clamps, so a bad sample cannot draw outside the plot', () => {
  const v = view([0, 1], [10, 999], [10, 10], 10)
  assert.ok(v.y(999) >= M.t && v.y(999) <= H - M.b)
  assert.ok(v.y(-5) <= H - M.b)
})

test('the area closes on the baseline and needs two points', () => {
  const v = view([0, 1, 2], [5, 4, 5], [5, 5, 5], 5)
  const d = areaPath(v, [5, 4, 5])
  assert.ok(d.startsWith(`M${M.l.toFixed(1)},`), 'opens on the baseline')
  assert.ok(d.endsWith('Z'), 'closed path')
  assert.equal(areaPath(view([0], [5], [5], 5), [5]), null)
})

test('empty input yields no view rather than NaN geometry', () => {
  assert.equal(view([], [], [], 0), null)
})

test('points are finite for a realistic LVHM series', () => {
  const total = seq(60, () => 1313)
  const online = seq(60, i => 1313 - (i > 20 && i < 40 ? 300 : 27))
  const v = view(seq(60, i => i), online, total, 1313)
  for (const p of points(v, online).split(' ')) {
    const [x, y] = p.split(',').map(Number)
    assert.ok(Number.isFinite(x) && Number.isFinite(y), p)
    assert.ok(y >= M.t - 1e-9 && y <= H - M.b + 1e-9, p)
  }
})
