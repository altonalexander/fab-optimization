/**
 * Regenerate docs/screenshots/*.png -- the images the README is built around.
 *
 *   node scripts/screenshots.mjs                       against http://127.0.0.1:5199
 *   node scripts/screenshots.mjs http://127.0.0.1:5199
 *   node scripts/screenshots.mjs https://fab.example.com ACCESSCODE
 *
 * Needs playwright (`npx playwright install chromium` once) and a dashboard
 * with a WARM mirror behind it. That second requirement is the real one: most
 * of these images are only worth having because the numbers in them are real,
 * and a mirror that started a minute ago reports zero dispatches, no recent
 * decisions and an empty event feed. Bring the stack up, let the feed run --
 * scripts/preview-ui.sh gives you one beside the live fab -- and check that
 * the fab is not paused before trusting the output.
 *
 * These were hand-captured before, which is why they went stale the moment the
 * UI changed. Anything the README shows should be reproducible by a command.
 */
import fs from 'fs'
import path from 'path'
import { createRequire } from 'module'
import { fileURLToPath } from 'url'

const HERE = path.dirname(fileURLToPath(import.meta.url))

// playwright is not a dependency of anything in this repo -- it is a tool you
// reach for when regenerating docs, not something the dashboard needs -- so it
// is resolved from wherever it happens to be installed rather than imported
// by bare name, which would only look beside THIS file and fail.
const { chromium } = await (async () => {
  for (const from of [path.join(HERE, '..', 'dispatch', 'ui', 'noop.js'),
                      path.join(HERE, '..', 'noop.js'),
                      path.join(process.cwd(), 'noop.js')]) {
    try {
      const req = createRequire(from)
      const m = await import(req.resolve('playwright'))
      // playwright is CommonJS, so importing it by path puts its exports on
      // `.default`. Accept either shape rather than depending on which.
      return m.chromium ? m : m.default
    } catch { /* try the next one */ }
  }
  console.error('playwright not found. Install it somewhere this can see:\n')
  console.error('  cd dispatch/ui && npm i -D playwright && npx playwright install chromium\n')
  process.exit(1)
})()
// FAB_SHOTS_OUT is for checking a capture before it overwrites the committed
// set -- point it at a scratch directory, look at what came out, then run it
// for real. Thirteen images is too many to review after the fact.
const OUT = process.env.FAB_SHOTS_OUT || path.join(HERE, '..', 'docs', 'screenshots')
const base = (process.argv[2] || 'http://127.0.0.1:5199').replace(/\/$/, '')
const code = process.argv[3] || process.env.FAB_ACCESS_CODE || ''
const W = 1440, H = 1000

// One entry per README image. `h` is the capture height in CSS pixels: these
// are bounded deliberately rather than captured fullPage, because under real
// load almost nothing on the tool index folds away and a full capture of it
// runs to five thousand pixels -- an image nobody scrolls and a file nobody
// wants in a git history. Pick a height that shows the point of the page.
// `prep` runs before the shot for the images that are of a particular STATE
// rather than a particular page.
const SHOTS = [
  { name: 'live',      path: '/live' },
  { name: 'live-controls', path: '/live', prep: async p => {
      // The playback popover, which is the subject of that section.
      const pill = await p.$('.live-wrap button.live')
      if (pill) { await pill.click(); await p.waitForTimeout(400) }
    } },
  { name: 'lots',      path: '/lots' },
  { name: 'lots-lotview', path: '/lots', prep: async p => {
      const lots = await p.$('button:has-text("lots")')
      if (lots) { await lots.click(); await p.waitForTimeout(1200) }
    } },
  { name: 'tools',     path: '/tools', h: 1500 },
  { name: 'tool',      path: null, resolve: async p => {
      // Whichever tool the fab is actually leaning on, rather than a hardcoded
      // id that may not exist in another dataset.
      const r = await p.request.get(`${base}/api/tools`)
      const d = await r.json()
      const fam = (d.groups || [])[0]
      return fam && fam.tools && fam.tools[0] ? `/tools/${fam.tools[0].id}` : '/tools'
    }, h: 1400 },
  { name: 'tool-changeovers', path: null, resolve: async p => {
      // The family that changes setup MOST, and its busiest tool -- changeovers
      // are a family total spread over its machines, so the first tool of the
      // first matching family is usually one that has never changed at all,
      // which is the opposite of what this image is for.
      const r = await p.request.get(`${base}/api/tools`)
      const d = await r.json()
      const fams = (d.groups || []).filter(g => g.setups && (g.changeovers || 0) > 0)
      if (!fams.length) {
        console.warn('  ! no family has changed setup yet -- let the feed run longer')
        return '/tools'
      }
      const fam = fams.reduce((a, g) => (g.changeovers > a.changeovers ? g : a))
      const tool = (fam.tools || []).reduce((a, t) => (t.dispatches > a.dispatches ? t : a))
      return `/tools/${tool.id}`
    }, h: 1900 },
  { name: 'floor',     path: '/floor?bay=8,2', h: 1400 },
  { name: 'products',  path: '/routes', h: 1200 },
  { name: 'routes',    path: null, resolve: async p => {
      const r = await p.request.get(`${base}/api/routes`)
      const d = await r.json().catch(() => ({}))
      const first = (d.products || d.routes || [])[0]
      const id = typeof first === 'string' ? first : first && (first.product || first.id)
      return id ? `/routes/${encodeURIComponent(id)}` : '/routes'
    }, h: 1500 },
  { name: 'slate',     path: '/slate', h: 1100 },
  { name: 'results',   path: '/results', h: 1600 },
  { name: 'topology',  path: '/topology', h: 1700 },
]

const b = await chromium.launch()
const ctx = await b.newContext({ viewport: { width: W, height: H } })

if (code) {
  const r = await ctx.request.post(`${base}/auth/code`, { data: { code } })
  if (!r.ok()) { console.error(`login failed: ${r.status()}`); process.exit(1) }
  console.log('logged in')
}

const p = await ctx.newPage()
const errs = []
p.on('pageerror', e => errs.push(String(e)))

// Refuse to write stale-looking images rather than quietly producing a set
// where every number is zero.
const state = await (await p.request.get(`${base}/api/state`)).json().catch(() => ({}))
if (state?.sim?.paused) {
  console.error('\nThe fab is PAUSED -- the event feed and recent decisions will be empty.')
  console.error('Resume it in the dashboard (or POST /api/sim/control) and re-run.')
  console.error('Pass --anyway to capture regardless.\n')
  if (!process.argv.includes('--anyway')) { await b.close(); process.exit(1) }
}

fs.mkdirSync(OUT, { recursive: true })
for (const s of SHOTS) {
  const target = s.resolve ? await s.resolve(p) : s.path
  await p.goto(base + target, { waitUntil: 'networkidle' })
  // The paused explainer covers everything; dismiss without changing the run.
  const keep = await p.$('button:has-text("Keep it paused")')
  if (keep) { await keep.click(); await p.waitForTimeout(300) }
  // The assistant's greeting bubble types itself in, so it would be caught
  // mid-word in every image and it covers the bottom-right corner of the one
  // panel most of these pages put there. Dismissed per shot rather than once:
  // it is re-offered on navigation.
  await p.waitForTimeout(1200)
  const hush = await p.$('.launcher-close')
  if (hush) { await hush.click().catch(() => {}); await p.waitForTimeout(300) }
  await p.waitForTimeout(1800)
  if (s.prep) await s.prep(p)
  const file = path.join(OUT, `${s.name}.png`)
  if (s.h && s.h !== H) {
    await p.setViewportSize({ width: W, height: s.h })
    await p.waitForTimeout(700)   // charts re-measure on resize
  }
  await p.screenshot({ path: file })
  if (s.h && s.h !== H) await p.setViewportSize({ width: W, height: H })
  console.log(`  ${s.name.padEnd(18)} ${target}`)
}

console.log(errs.length ? `\npage errors:\n  ${errs.join('\n  ')}` : '\nno page errors')
await b.close()
