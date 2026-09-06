/**
 * URL round-trip checks for the path router.
 *
 *   node src/router.test.mjs
 *
 * Pure functions only -- parseUrl and buildUrl are what decide whether a
 * pasted link lands on the view it was copied from, so they are worth pinning
 * without a browser in the loop.
 */
import assert from 'node:assert/strict'
import { parseUrl, buildUrl, isAppPath, TABS } from './router.js'

const cases = [
  ['/', '', '/live', {}],
  ['', '', '/live', {}],
  ['/tools', '', '/tools', {}],
  ['/tools/ETCH_11', '', '/tools/ETCH_11', {}],
  ['/tools', '?q=etch&type=ETCH&delay=1', '/tools', { q: 'etch', type: 'ETCH', delay: '1' }],
  ['/floor', '?bay=3%2C2&heat=1', '/floor', { bay: '3,2', heat: '1' }],
]
for (const [pathname, search, path, query] of cases) {
  const r = parseUrl(pathname, search)
  assert.equal(r.path, path, `path for ${pathname || '(empty)'}`)
  assert.deepEqual(r.query, query, `query for ${pathname || '(empty)'}`)
}

assert.deepEqual(parseUrl('/tools/ETCH_11').segments, ['tools', 'ETCH_11'])
assert.deepEqual(parseUrl('/tools/CD%20SEM_01').segments, ['tools', 'CD SEM_01'])

// build <-> parse round trip, including a segment that contains a slash
assert.equal(buildUrl('/tools'), '/tools')
assert.equal(buildUrl(['tools', 'CD SEM_01']), '/tools/CD%20SEM_01')
assert.equal(buildUrl(['tools', 'A/B']), '/tools/A%2FB')
assert.deepEqual(parseUrl(buildUrl(['tools', 'A/B'])).segments, ['tools', 'A/B'])
assert.equal(buildUrl('/tools', { q: 'etch', type: '', delay: false, hot: true }), '/tools?q=etch&hot=1')
assert.equal(buildUrl('/lots', { cohort: 'part_10-d58' }), '/lots?cohort=part_10-d58')

// what the click interceptor treats as in-app
for (const t of TABS) assert.ok(isAppPath(`/${t}`), t)
assert.ok(isAppPath('/tools/ETCH_11?q=1'))
assert.ok(isAppPath('/'))
for (const h of ['/admin', '/auth/logout', '/docs', '/openapi.json', 'https://x.y/', '//evil', '#/live']) {
  assert.ok(!isAppPath(h), `${h} must be a real navigation`)
}

console.log('router ok')
