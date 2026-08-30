import assert from 'node:assert/strict'
import test from 'node:test'
import {
  M, TICKS, view, timeView, spanFor, niceMax, niceStep, yTicks, points,
  indexAt, xTickIndices, fmtSimTime, fmtSpan, travel,
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

// --- simulated-time mode ----------------------------------------------------

const tv = (ts, span, vals = ts.map(() => 1)) => timeView(ts, span, 300, 100, [vals])

test('the newest sample anchors the right edge in time mode too', () => {
  const v = tv([100, 160, 220], 600)
  assert.equal(Number(v.x(2).toFixed(6)), Number((M.l + v.iw).toFixed(6)))
})

test('x distance is elapsed fab time, not sample count', () => {
  // Three samples, but the second gap is five times the first: the plot has to
  // show that gap, or a stall in the fab looks like ordinary spacing.
  const v = tv([0, 60, 360], 600)
  const d1 = v.x(1) - v.x(0)
  const d2 = v.x(2) - v.x(1)
  assert.ok(Math.abs(d2 / d1 - 5) < 1e-9, 'the two gaps must be in 1:5 ratio')
})

test('a pause holds the chart still instead of scrolling out a flat line', () => {
  // Paused, the feed keeps heartbeating but the fab clock does not advance, so
  // every sample lands on the same x. Nothing moves -- which is what happened.
  const v = tv([0, 60, 120, 120, 120], 600)
  assert.equal(v.x(4), v.x(3))
  assert.equal(v.x(3), v.x(2))
})

test('spanFor measures the fab-time window instead of trusting the speed dial', () => {
  assert.equal(spanFor([0, 5, 10], 11, 0), 50, '5 sim-s per sample over 10 slots')
  // 400x: the same arrivals now carry 2000 sim-seconds each.
  assert.equal(spanFor([0, 2000, 4000], 11, 0), 20000)
})

test('spanFor keeps the last window through a pause', () => {
  // All-equal timestamps: paused. A measured span of zero would collapse the
  // axis and put every sample on the right edge.
  assert.equal(spanFor([9, 9, 9], 11, 600), 600)
  assert.equal(spanFor([], 11, 600), 600)
  assert.ok(spanFor([], 11, 0) > 0, 'never a zero span, even with nothing to measure')
})

test('spanFor ignores a pause at the tail and uses the last real advance', () => {
  assert.equal(spanFor([0, 5, 10, 10, 10], 11, 0), 50)
})

test('a speed change rescales the window rather than distorting one axis', () => {
  // The honest consequence of plotting fab time: at 400x the ten minutes you
  // watched at 1x really is a sliver of the new window. It compresses, it does
  // not silently restretch, and the old samples keep their true spacing.
  const slow = tv([0, 5, 10], spanFor([0, 5, 10], 11))
  const fast = tv([0, 5, 10], spanFor([0, 2000, 4000], 11))
  assert.ok(fast.span > slow.span * 100)
  const width = v => Math.abs(v.x(2) - v.x(0))
  assert.ok(width(fast) < width(slow) / 100,
            'history from the slow window collapses toward the edge')
})

test('indexAt finds the sample under the pointer in time mode', () => {
  const v = tv([0, 60, 360], 600)
  for (let i = 0; i < 3; i++) assert.equal(indexAt(v, v.x(i)), i)
  assert.equal(indexAt(v, v.x(0) - 200), null, 'empty stretch reads as nothing')
})

test('time-mode x labels are spaced apart and always label the newest', () => {
  const ts = seq(40, i => i * 60)
  const v = tv(ts, spanFor(ts, 40))
  const idx = xTickIndices(v, 70)
  assert.equal(idx[idx.length - 1], v.n - 1)
  for (let i = 1; i < idx.length; i++) {
    assert.ok(v.x(idx[i]) - v.x(idx[i - 1]) >= 70 - 1e-9)
  }
})

// --- what actually animates ---------------------------------------------------

test('one sample slides by one slot in index mode', () => {
  const v = mk(6)
  assert.equal(travel({ at: 5, span: 0 }, { at: 6, span: 0 }, v, false), v.slot)
})

test('in time mode the slide is the fab time that elapsed', () => {
  const ts = seq(5, i => i * 60)
  const v = tv(ts, spanFor(ts, 5))
  // Half the window of fab time should move the plot half its width.
  const half = travel({ at: 0, span: v.span }, { at: v.span / 2, span: v.span },
                      v, true)
  assert.ok(Math.abs(half - v.iw / 2) < 1e-9)
})

test('a pause slides by nothing at all', () => {
  const ts = [0, 60, 120, 120]
  const v = tv(ts, spanFor(ts, 5))
  assert.equal(travel({ at: 120, span: v.span }, { at: 120, span: v.span },
                      v, true), 0)
})

test('a speed change snaps instead of sliding', () => {
  // The window rescaled underneath it, so px before and px after measure
  // different amounts of fab time and any translate between them is a lie.
  const ts = seq(5, i => i * 60)
  const v = tv(ts, spanFor(ts, 5))
  assert.equal(travel({ at: 100, span: 240 }, { at: 160, span: 96000 }, v, true), 0)
})

test('a reset or a new run does not fly in from the side', () => {
  const ts = seq(5, i => i * 60)
  const v = tv(ts, spanFor(ts, 5))
  assert.equal(travel({ at: 900, span: v.span }, { at: 60, span: v.span },
                      v, true), 0, 'clock went backwards: a new run')
  assert.equal(travel({ at: 0, span: 0 }, { at: 1, span: 0 }, mk(1), false), 0,
               'a single sample is not a step')
})

test('a reconnect after a long gap snaps rather than scrolling the whole width', () => {
  const ts = seq(5, i => i * 60)
  const v = tv(ts, spanFor(ts, 5))
  assert.equal(travel({ at: 0, span: v.span },
                      { at: v.span * 3, span: v.span }, v, true), 0)
})

test('fab time is labelled in the days the burndown already uses', () => {
  assert.equal(fmtSimTime(12.4 * 86400), 'd12.4')
  assert.equal(fmtSpan(600), '10 fab min')
  assert.equal(fmtSpan(240000), '2.8 fab days')
  assert.equal(fmtSpan(0), '—')
})
