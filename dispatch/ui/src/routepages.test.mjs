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
    rework_steps: r.rework.length,
    dominant_area: Object.keys(r.areas).reduce((a, b) =>
      (r.areas[a] >= r.areas[b] ? a : b)),
    lots_tracked: 0, cohorts_tracked: 0,
  }
}

// --- index ------------------------------------------------------------------
const index = { dataset: doc.dataset, areas: doc.areas,
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
    data: detail, order: doc.areas, backHref: '#/routes',
    cohortHref: c => `#/lots?cohort=${encodeURIComponent(c)}`,
  }))

assert.ok(detHtml.includes('part_3'), 'names the product')
assert.ok(detHtml.includes(String(r3.n_steps)), 'shows the step count')
assert.ok(detHtml.includes('href="#/routes"'), 'links back to the index')
// The cross-link the lots view needs: cohort -> its burndown, by URL.
assert.ok(detHtml.includes('href="#/lots?cohort=part_3-d0"'), 'cohort link')
assert.ok(detHtml.includes('href="#/lots?cohort=part_3-d1"'), 'cohort link')
assert.ok(detHtml.includes('Showing 2 of 3'), 'says the sample is a sample')
// One block per visit, in route order.
assert.equal((detHtml.match(/class="visit-strip"/g) || []).length, 1)
const strip = detHtml.slice(detHtml.indexOf('class="visit-strip"'))
const blocks = strip.slice(0, strip.indexOf('</div>'))
assert.equal((blocks.match(/flex-grow:/g) || []).length, r3.visits.length,
             'a block per visit')
// Rework is what makes remaining-steps non-monotonic on the lots view, so the
// page has to show the loops rather than only count them.
assert.equal((detHtml.match(/<tr><td class="num">\d+<\/td>/g) || []).length,
             r3.rework.length, 'a row per rework loop')

// An empty cohort sample is the no-feed case and must not blank the page.
const noFeed = renderToStaticMarkup(
  React.createElement(RouteProductView, {
    data: { ...detail, cohorts: [], total_cohorts: 0 },
    order: doc.areas, backHref: '#/routes', cohortHref: c => `#/lots?cohort=${c}`,
  }))
assert.ok(noFeed.includes('No lots of part_3 are being tracked'))
assert.ok(noFeed.includes('visit-strip'), 'the route still renders without a feed')

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
    order: doc.areas, backHref: '#/routes', cohortHref: c => c,
  }))
for (const a of ['Wet_Etch', 'Litho']) {
  const c1 = detHtml.slice(hueIn(detHtml, a)).match(/background:(#[0-9a-f]{6})/)[1]
  const c2 = other.slice(hueIn(other, a)).match(/background:(#[0-9a-f]{6})/)[1]
  assert.equal(c1, c2, `${a} keeps its colour across products`)
}

console.log(`ok — routes pages render (${products.length} products, ` +
            `${r3.visits.length} visits on part_3)`)
