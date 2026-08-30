/**
 * Render checks for the routes pages.
 *
 *   node src/routepages.test.mjs
 *
 * Renders RouteIndexView and RouteProductView to static markup against the
 * real SMT2020 route data (viz/routes_lvhm.json), shaped exactly as
 * /api/routes and /api/routes/<product> shape it. No browser and no API: the
 * views were split from their fetching shells so they render from a plain
 * object, and what this pins is what a product page actually says -- its step
 * counts, its rework loops, and that every cohort links to the lots view while
 * every product links to its own URL.
 */
import assert from 'node:assert/strict'
import fs from 'node:fs'
import { fileURLToPath } from 'node:url'
import { createRequire } from 'node:module'
import { renderToStaticMarkup } from 'react-dom/server'
import { transformSync } from 'esbuild'
import React from 'react'

const require_ = createRequire(import.meta.url)
const here = fileURLToPath(new URL('.', import.meta.url))

// esbuild (a vite dependency) transpiles the JSX in-process, so the test needs
// no build step and no extra devDependency. The automatic runtime is used
// because the source, like the rest of the app, does not import React itself.
const src = fs.readFileSync(here + 'RoutePages.jsx', 'utf8')
const js = transformSync(src, { loader: 'jsx', format: 'cjs', target: 'node18', jsx: 'automatic' }).code
const mod = { exports: {} }
new Function('require', 'module', 'exports', js)(
  name => (name === 'react' ? React : require_(name)), mod, mod.exports)
const { RouteIndexView, RouteProductView } = mod.exports

const doc = JSON.parse(fs.readFileSync(here + '../../../viz/routes_lvhm.json', 'utf8'))
const products = Object.keys(doc.routes).map(k => [`part_${k}`, k])
  .sort((a, b) => Number(a[1]) - Number(b[1]))

const summary = ([product, key]) => {
  const r = doc.routes[key]
  return {
    product, key, route: r.id, n_steps: r.n_steps, n_visits: r.n_visits,
    n_areas: Object.keys(r.areas).length, areas: r.areas,
    batch_steps: r.batch_steps, sampled: r.sampled,
    measure_steps: r.measure_steps, rework_steps: r.rework.length,
    dominant_area: Object.keys(r.areas).reduce((a, b) =>
      (r.areas[a] >= r.areas[b] ? a : b)),
    lots_tracked: 0, cohorts_tracked: 0,
  }
}

// --- index ------------------------------------------------------------------
const ZONES = [
  { name: 'Thermal / Deposition', areas: ['Diffusion', 'Dielectric', 'TF', 'TF_Met'] },
  { name: 'Patterning', areas: ['Litho', 'Litho_Met'] },
  { name: 'Etch', areas: ['Dry_Etch', 'Wet_Etch'] },
  { name: 'Doping & Planarization', areas: ['Implant', 'Planar'] },
  { name: 'Metrology & Queue', areas: ['Def_Met', 'Delay_32', 'Delay'] },
]

const index = { dataset: doc.dataset, areas: doc.areas, zones: ZONES,
                products: products.map(summary) }
index.products[2].lots_tracked = 9
index.products[2].cohorts_tracked = 2

const idxHtml = renderToStaticMarkup(
  React.createElement(RouteIndexView,
                      { data: index, hrefFor: id => `#/routes/${id}` }))

// Every product is its own address -- the whole point of the rewrite.
for (const [product] of products) {
  assert.ok(idxHtml.includes(`href="#/routes/${product}"`), `link for ${product}`)
}
assert.equal((idxHtml.match(/class="route-card"/g) || []).length, 10)
assert.ok(idxHtml.includes('SMT2020_LVHM'))
assert.ok(idxHtml.includes('2 cohorts · 9 lots live'), 'live counts on the card')
assert.ok(idxHtml.includes('no lots tracked'), 'idle products say so')
// Bars are scaled to the longest route, so the longest one -- and only it --
// fills its track.
const widths = [...idxHtml.matchAll(/width:([\d.]+)%/g)].map(m => Number(m[1]))
assert.ok(Math.max(...widths) <= 100.001, 'no bar overflows its track')
const longest = Math.max(...index.products.map(p => p.n_steps))
const shortest = Math.min(...index.products.map(p => p.n_steps))
assert.ok(longest > shortest, 'routes differ in length')

// --- one product ------------------------------------------------------------
const r3 = doc.routes['3']
const detail = Object.assign(summary(['part_3', '3']), {
  dataset: doc.dataset, visits: r3.visits, transitions: r3.transitions,
  rework: r3.rework, area_visits: r3.area_visits, now_t: 90000, warm_t: 0,
  total_cohorts: 3,
  cohorts: [
    { cohort: 'part_3-d0', part: 'part_3', lots: 5, done: 1, min_left: 40,
      med_left: 52, max_left: 91, spread: 51, release: 0, due: 4320000 },
    { cohort: 'part_3-d1', part: 'part_3', lots: 4, done: 0, min_left: 300,
      med_left: 300, max_left: 300, spread: 0, release: 86400, due: 4406400 },
  ],
})

const detHtml = renderToStaticMarkup(
  React.createElement(RouteProductView, {
    data: detail, order: doc.areas, zones: ZONES, backHref: '#/routes',
    cohortHref: c => `#/lots?cohort=${encodeURIComponent(c)}`,
  }))

assert.ok(detHtml.includes('part_3'), 'names the product')
assert.ok(detHtml.includes(String(r3.n_steps)), 'shows the step count')
assert.ok(detHtml.includes('href="#/routes"'), 'links back to the index')
// The cross-link the lots view needs: cohort -> its burndown, by URL.
assert.ok(detHtml.includes('href="#/lots?cohort=part_3-d0"'), 'cohort link')
assert.ok(detHtml.includes('href="#/lots?cohort=part_3-d1"'), 'cohort link')
assert.ok(detHtml.includes('Showing 2 of 3'), 'says the sample is a sample')
// --- the lane map -----------------------------------------------------------
// One lane per area this route visits, in zone order, and one block per visit.
const lanes = ZONES.flatMap(z => z.areas).filter(a => r3.area_visits[a])
assert.equal(lanes.length, Object.keys(r3.areas).length, 'every bay gets a lane')
const map = detHtml.slice(detHtml.indexOf('class="lane-map"'))
const svg = map.slice(0, map.indexOf('</svg>'))
// Lane labels appear in zone order, not in the route's own or alphabetical
// order -- that ordering is what makes the litho/etch loop read as one band.
const labelAt = a => svg.indexOf(`>${a}</text>`)
for (const a of lanes) assert.ok(labelAt(a) > 0, `lane label for ${a}`)
const positions = lanes.map(labelAt)
assert.deepEqual(positions, [...positions].sort((x, y) => x - y),
                 'lanes are laid out in zone order')
// A block per visit, plus one lane background per lane.
assert.equal((svg.match(/<rect /g) || []).length,
             r3.visits.length + lanes.length, 'a block per visit')

// Measurement lanes are labelled as such, and only those.
const measured = lanes.filter(a => a.endsWith('_Met'))
assert.equal((svg.match(/>measure<\/text>/g) || []).length, measured.length,
             'every metrology lane is labelled, and no other')
assert.ok(measured.length >= 3, 'LVHM measures in at least three bays')

// Rework: an accent block on the triggering visit and a marker under the axis.
assert.equal((svg.match(/<polygon /g) || []).length, r3.rework.length,
             'a marker per rework point')
const REWORK_FILL = '#ea580c'
const accentBlocks = (svg.match(new RegExp(`fill="${REWORK_FILL}"`, 'g')) || [])
// Each rework point paints its visit block and its axis marker; two rework
// steps can share one visit, so this is an upper bound rather than exact.
assert.ok(accentBlocks.length >= r3.rework.length,
          'rework points are drawn in the accent')
assert.ok(r3.rework.every(w => w.area === 'Litho_Met'),
          'the copy claims every rework point is a litho metrology step')
assert.ok(detHtml.includes('Every rework point on this route is a'),
          'and the page says so')

// The tooltip on a sampled visit states the rate, since a sampled step
// measures only a fraction of lots and the nominal route overstates it.
const sampled = r3.visits.find(v => v.sampled && v.pct != null)
assert.ok(sampled, 'route 3 has sampled visits')
assert.ok(svg.includes(`run on ${sampled.pct}% of lots`), 'sampling rate shown')

// The legend counts what the map colours.
assert.ok(detHtml.includes(`${detail.measure_steps} of ${detail.n_steps} steps measure`),
          'legend states the measurement share')
// Rework is what makes remaining-steps non-monotonic on the lots view, so the
// page has to show the loops rather than only count them.
assert.equal((detHtml.match(/<tr><td class="num">\d+<\/td>/g) || []).length,
             r3.rework.length, 'a row per rework loop')

// An empty cohort sample is the no-feed case and must not blank the page.
const noFeed = renderToStaticMarkup(
  React.createElement(RouteProductView, {
    data: { ...detail, cohorts: [], total_cohorts: 0 },
    order: doc.areas, zones: ZONES, backHref: '#/routes',
    cohortHref: c => `#/lots?cohort=${c}`,
  }))
assert.ok(noFeed.includes('No lots of part_3 are being tracked'))
assert.ok(noFeed.includes('lane-map'), 'the route still renders without a feed')

// Colours are assigned from the global area order, so the same bay is the same
// colour on every product page.
// The area's own row in the steps table -- not the `dominant_area` stat above
// it, which carries no swatch.
const hueIn = (html, area) => {
  const i = html.indexOf(`>${area}</td>`)
  assert.ok(i > 0, `${area} has a row`)
  return html.lastIndexOf('background:', i)
}
const other = renderToStaticMarkup(
  React.createElement(RouteProductView, {
    data: Object.assign(summary(['part_7', '7']), {
      visits: doc.routes['7'].visits, rework: doc.routes['7'].rework,
      area_visits: doc.routes['7'].area_visits, transitions: [], cohorts: [],
      total_cohorts: 0,
    }),
    order: doc.areas, zones: ZONES, backHref: '#/routes', cohortHref: c => c,
  }))
for (const a of ['Wet_Etch', 'Litho']) {
  const c1 = detHtml.slice(hueIn(detHtml, a)).match(/background:(#[0-9a-f]{6})/)[1]
  const c2 = other.slice(hueIn(other, a)).match(/background:(#[0-9a-f]{6})/)[1]
  assert.equal(c1, c2, `${a} keeps its colour across products`)
}

// A route whose grouping is missing must still draw every lane, or a dataset
// with a new bay would silently lose it.
const noZones = renderToStaticMarkup(
  React.createElement(RouteProductView, {
    data: { ...detail, cohorts: [], total_cohorts: 0 }, order: doc.areas,
    zones: [], backHref: '#/routes', cohortHref: c => c,
  }))
for (const a of lanes) {
  assert.ok(noZones.includes(`>${a}</text>`), `${a} survives a missing grouping`)
}

console.log(`ok — routes pages render (${products.length} products; part_3: ` +
            `${r3.visits.length} visits, ${lanes.length} lanes, ` +
            `${measured.length} measurement, ${r3.rework.length} rework)`)
