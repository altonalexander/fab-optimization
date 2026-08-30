import assert from 'node:assert/strict'
import test from 'node:test'
import {
  M, TICKS, view, niceMax, niceStep, yTicks, points, indexAt, xTickIndices,
} from './stream_geom.js'

const seq = (n, f) => Array.from({ length: n }, (_, i) => f(i))
const mk = (n, cap = 10, vals = seq(n, () => 1)) => view(n, cap, 300, 100, [vals])

test('the newest sample is on the right edge whatever the window holds', () => {
  for (const n of [1, 2, 5, 10]) {
    const v = mk(n)
    assert.equal(Number(v.x(n - 1).toFixed(6)), Number((M.l + v.iw).toFixed(6)))
  }
})

test('the slot width does not depend on how many samples have arrived', () => {
  // This is the whole fix. If the slot changed with n, every point would move
  // by a different amount on every sample and the series would squirm rather
  // than scroll -- no single translate could stand in for the redraw.
  assert.equal(mk(3).slot, mk(9).slot)
})

test('one more sample shifts every existing sample left by exactly one slot', () => {
  const a = mk(5), b = mk(6)
  assert.equal(a.slot, b.slot)
  for (let i = 0; i < 5; i++) {
    // sample i in the 5-point view is sample i in the 6-point view, one slot on.
    assert.ok(Math.abs(a.x(i) - (b.x(i) + b.slot)) < 1e-9)
  }
})

test('the y domain is stepped, so it holds still across ordinary jitter', () => {
  const a = view(3, 10, 300, 100, [[41, 44, 43]])
  const b = view(3, 10, 300, 100, [[41, 44, 47]])
  assert.equal(a.yMax, b.yMax, 'a wiggle under the step must not move the axis')
  assert.ok(a.yMax >= 44 * 1.1, 'the peak stays inside the plot with headroom')
})

test('the domain is zero-based and clamps out-of-range values', () => {
  const v = mk(3, 10, [0, 5, 10])
  assert.equal(v.y(0), M.t + v.ih)
  assert.equal(v.y(v.yMax), M.t)
  assert.equal(v.y(-5), v.y(0))
  assert.equal(v.y(v.yMax * 2), v.y(v.yMax))
})

test('niceStep rounds up to 1/2/5 x 10^k', () => {
  assert.equal(niceStep(0.4), 0.5)
  assert.equal(niceStep(3), 5)
  assert.equal(niceStep(11), 20)
  assert.equal(niceStep(640), 1000)
})

test('niceMax covers the value with round gridlines, and never overshoots wildly', () => {
  assert.equal(niceMax(0), TICKS, 'an empty chart still has an axis')
  assert.equal(niceMax(-3), TICKS)
  for (const v of [0.3, 3, 11, 47, 118, 640, 5200]) {
    const m = niceMax(v)
    assert.ok(m >= v, `${m} must cover ${v}`)
    assert.ok(m <= v * 2, `${m} wastes more than half the plot on ${v}`)
    assert.equal(niceStep(m / TICKS) * TICKS, m, 'gridlines land on round numbers')
  }
})

test('y ticks span the domain inclusively', () => {
  const t = yTicks(mk(3, 10, [0, 7, 3]))
  assert.equal(t[0], 0)
  assert.equal(t[t.length - 1], mk(3, 10, [0, 7, 3]).yMax)
})

test('gaps in a series are dropped, not drawn through zero', () => {
  const v = mk(3, 10, [1, 2, 3])
  const p = points(v, [1, null, 3])
  assert.equal(p.split(' ').length, 2)
  assert.ok(!p.includes('NaN'))
})

test('indexAt round-trips the sample under the pointer, and rejects misses', () => {
  const v = mk(6)
  for (let i = 0; i < 6; i++) assert.equal(indexAt(v, v.x(i)), i)
  assert.equal(indexAt(v, v.x(0) - v.slot * 2), null, 'left of the oldest sample')
  assert.equal(indexAt(v, v.x(5) + v.slot * 2), null, 'right of the newest')
})

test('x tick labels are spaced at least the requested gap apart', () => {
  const v = mk(10)
  const idx = xTickIndices(v, 70)
  for (let i = 1; i < idx.length; i++) {
    assert.ok(v.x(idx[i]) - v.x(idx[i - 1]) >= 70 - 1e-9)
  }
  // The newest sample is always labelled: it is the one being read.
  assert.equal(idx[idx.length - 1], v.n - 1)
})

test('a degenerate box still produces finite geometry', () => {
  const v = view(3, 10, 0, 0, [[1, 2, 3]])
  assert.ok(Number.isFinite(v.x(0)) && Number.isFinite(v.y(1)) && v.slot > 0)
})
