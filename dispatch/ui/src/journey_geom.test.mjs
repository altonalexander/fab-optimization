import assert from 'node:assert/strict'
import test from 'node:test'
import { widths, MIN_SHARE, famLabel, fmtProc, statusOf } from './journey_geom.js'

const near = (a, b) => Math.abs(a - b) < 1e-9

test('widths follow process time and sum to one', () => {
  const w = widths([{ proc_s: 600 }, { proc_s: 1800 }, { proc_s: 600 }])
  assert.ok(near(w.reduce((a, b) => a + b, 0), 1))
  // Above the legibility floor, width is proportional to process time.
  assert.ok(near(w[1] - MIN_SHARE, 3 * (w[0] - MIN_SHARE)))
})

test('a tiny step still gets a legible box', () => {
  const w = widths([{ proc_s: 2 }, { proc_s: 7200 }])
  assert.ok(w[0] >= MIN_SHARE - 1e-9)
  assert.ok(near(w[0] + w[1], 1))
})

test('unknown times share the strip evenly', () => {
  const w = widths([{}, {}, {}, {}])
  assert.ok(w.every(v => near(v, 0.25)))
  assert.deepEqual(widths([]), [])
})

test('labels and durations read cleanly', () => {
  assert.equal(famLabel('Litho_FE_115'), 'Litho FE 115')
  assert.equal(fmtProc(7500), '2h 05m')
  assert.equal(fmtProc(90), '1m')
  assert.equal(fmtProc(30), '30s')
})

test('status names the lot position', () => {
  assert.equal(statusOf({ idx: 5, n: 10, tool: 'X' }), 'on tool')
  assert.equal(statusOf({ idx: 5, n: 10, waiting: true }), 'waiting')
  assert.equal(statusOf({ idx: 10, n: 10 }), 'done')
  assert.equal(statusOf({ idx: 5, n: 10 }), 'in transit')
  assert.equal(statusOf(null), 'unknown')
})
