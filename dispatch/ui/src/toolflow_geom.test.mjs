import assert from 'node:assert/strict'
import test from 'node:test'
import {
  GRID, queueCells, ringLayout, recentOut, shortId, simNow, remaining,
  progress, fmtCountdown, setupLabel,
} from './toolflow_geom.js'

const ids = n => Array.from({ length: n }, (_, i) => `Lot_1_${i}`)

test('a queue that fits shows every lot and pads with empty cells', () => {
  const cells = queueCells(ids(4), 4)
  assert.equal(cells.length, GRID)
  assert.deepEqual(cells.slice(0, 4).map(c => c.kind), ['lot', 'lot', 'lot', 'lot'])
  assert.ok(cells.slice(4).every(c => c.kind === 'empty'))
})

test('exactly nine lots fill the grid with no +n cell', () => {
  const cells = queueCells(ids(9), 9)
  assert.ok(cells.every(c => c.kind === 'lot'))
})

test('the ninth cell becomes +n and accounts for the whole queue', () => {
  const cells = queueCells(ids(12), 57)
  assert.equal(cells[8].kind, 'more')
  // 8 drawn, 49 not: the grid still sums to the 57 the stat tile shows.
  assert.equal(cells[8].n, 49)
  assert.equal(cells.slice(0, 8).filter(c => c.kind === 'lot').length, 8)
})

test('a capped prefix still reports the true remainder', () => {
  // The API sends 12 ids for a 30-deep queue; +n must use 30, not 12.
  const cells = queueCells(ids(12), 30)
  assert.equal(cells[8].n, 22)
})

test('one lot sits in the middle of the box', () => {
  assert.deepEqual(ringLayout(1), { points: [{ x: 0.5, y: 0.5 }], extra: 0 })
})

test('a batch is ringed, starting at twelve o\'clock, all inside the box', () => {
  const { points, extra } = ringLayout(6)
  assert.equal(points.length, 6)
  assert.equal(extra, 0)
  assert.ok(Math.abs(points[0].x - 0.5) < 1e-9 && points[0].y < 0.5)
  for (const p of points) {
    assert.ok(p.x > 0.1 && p.x < 0.9 && p.y > 0.1 && p.y < 0.9, JSON.stringify(p))
  }
})

test('past the ring cap the rest is counted, not drawn', () => {
  const { points, extra } = ringLayout(11)
  assert.equal(points.length, 8)
  assert.equal(extra, 3)
})

test('recent out lists three and counts the rest', () => {
  const rows = Array.from({ length: 7 }, (_, i) => ({ lot: `Lot_2_${i}` }))
  const { shown, extra } = recentOut(rows)
  assert.equal(shown.length, 3)
  assert.equal(extra, 4)
  assert.deepEqual(recentOut([]), { shown: [], extra: 0 })
})

test('lot ids shorten to their index and hot lots are flagged', () => {
  assert.deepEqual(shortId('Lot_3_1234'), { short: '1234', hot: false })
  assert.deepEqual(shortId('HotLot_4_88'), { short: '88', hot: true })
  assert.deepEqual(shortId('weird'), { short: 'weird', hot: false })
})

test('the sim clock advances at playback speed and holds while paused', () => {
  const clock = { t: 1000, t_at: 50, speed: 20, paused: false }
  assert.equal(simNow(clock, 52), 1040)
  assert.equal(simNow({ ...clock, paused: true }, 52), 1000)
  // A wall clock behind the reading (skew) never rewinds the fab clock.
  assert.equal(simNow(clock, 40), 1000)
  assert.equal(simNow({ t: null }, 52), null)
})

test('remaining counts down to zero and stops', () => {
  const clock = { t: 1000, t_at: 0, speed: 1, paused: false }
  assert.equal(remaining({ t: 900, end: 1300 }, clock, 100), 200)
  assert.equal(remaining({ t: 900, end: 1300 }, clock, 1000), 0)
  assert.equal(remaining({ t: 900 }, clock, 0), null)
  assert.equal(remaining(null, clock, 0), null)
})

test('progress is the elapsed share of the run, clamped', () => {
  const clock = { t: 1000, t_at: 0, speed: 1, paused: false }
  assert.equal(progress({ t: 800, end: 1200 }, clock, 0), 0.5)
  assert.equal(progress({ t: 800, end: 1200 }, clock, 5000), 1)
  assert.equal(progress({ t: 800, end: 800 }, clock, 0), null)
})

test('countdowns read in at most two units', () => {
  assert.equal(fmtCountdown(2 * 3600 + 5 * 60 + 9), '2h 05m')
  assert.equal(fmtCountdown(4 * 60 + 30), '4m 30s')
  assert.equal(fmtCountdown(12), '12s')
  assert.equal(fmtCountdown(0), '0s')
  assert.equal(fmtCountdown(null), '—')
})

test('the feed\'s "-" setup means no setup', () => {
  assert.equal(setupLabel('-'), null)
  assert.equal(setupLabel(''), null)
  assert.equal(setupLabel(null), null)
  assert.equal(setupLabel('S_12'), 'S_12')
})
