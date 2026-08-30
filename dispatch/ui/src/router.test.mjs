/**
 * URL round-trip checks for the hash router.
 *
 *   node src/router.test.mjs
 *
 * Pure functions only -- parseHash and buildHash are what decide whether a
 * pasted link lands on the view it was copied from, so they are worth pinning
 * without a browser in the loop.
 */
import assert from 'node:assert/strict'
import { parseHash, buildHash, TABS } from './router.js'

const cases = [
  ['', '/live', {}],
  ['#', '/live', {}],
  ['#/', '/live', {}],
  ['#/tools', '/tools', {}],
  ['#/tools/ETCH_11', '/tools/ETCH_11', {}],
  ['#/tools?q=etch&type=ETCH&delay=1', '/tools', { q: 'etch', type: 'ETCH', delay: '1' }],
  ['#/floor?bay=3%2C2&heat=1', '/floor', { bay: '3,2', heat: '1' }],
]
for (const [hash, path, query] of cases) {
  const r = parseHash(hash)
  assert.equal(r.path, path, `path for ${hash || '(empty)'}`)
  assert.deepEqual(r.query, query, `query for ${hash || '(empty)'}`)
}

assert.deepEqual(parseHash('#/tools/ETCH_11').segments, ['tools', 'ETCH_11'])
assert.deepEqual(parseHash('#/tools/CD%20SEM_01').segments, ['tools', 'CD SEM_01'])

// Empty and false values never reach the bar.
assert.equal(buildHash('/tools', { q: '', type: undefined, delay: false }), '#/tools')
assert.equal(buildHash('/tools', { q: 'etch' }), '#/tools?q=etch')
assert.equal(buildHash('/floor', { bay: '3,2', heat: true }), '#/floor?bay=3%2C2&heat=1')

// Round trip: every tab, and a tool id with a comma in it, survive both ways.
for (const t of TABS) assert.equal(parseHash(buildHash(`/${t}`)).path, `/${t}`)
for (const id of ['ETCH_11', 'CD SEM_01', 'a/b', 'x?y']) {
  const r = parseHash(buildHash(['tools', id]))
  assert.deepEqual(r.segments, ['tools', id], `round trip ${id}`)
}

console.log(`ok — ${cases.length + TABS.length + 9} router assertions`)
