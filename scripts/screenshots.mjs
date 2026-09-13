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
const OUT = path.join(HERE, '..', 'docs', 'screenshots')
const base = (process.argv[2] || 'http://127.0.0.1:5199').replace(/\/$/, '')
const code = process.argv[3] || process.env.FAB_ACCESS_CODE || ''
const W = 1440, H = 1000

// One entry per README image. `full` captures the whole scrolling page (the
// index pages earn it; a live chart does not). `prep` runs before the shot for
// the images that are of a particular STATE rather than a particular page.
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
  { name: 'tools',     path: '/tools', full: true },
  { name: 'tool',      path: null, resolve: async p => {
      // Whichever tool the fab is actually leaning on, rather than a hardcoded
      // id that may not exist in another dataset.
      const r = await p.request.get(`${base}/api/tools`)
      const d = await r.json()
      const fam = (d.groups || [])[0]
      return fam && fam.tools && fam.tools[0] ? `/tools/${fam.tools[0].id}` : '/tools'
    }, full: true },
  { name: 'tool-changeovers', path: null, resolve: async p => {
      // A tool from a family that actually changes setup, since that is what
      // the image is meant to show.
      const r = await p.request.get(`${base}/api/tools`)
      const d = await r.json()
      const fam = (d.groups || []).find(g => g.setups && g.changeovers > 0)
                || (d.groups || []).find(g => g.setups)
      return fam && fam.tools && fam.tools[0] ? `/tools/${fam.tools[0].id}` : '/tools'
    }, full: true },
  { name: 'floor',     path: '/floor?bay=8,2', full: true },
  { name: 'products',  path: '/routes', full: true },
  { name: 'routes',    path: null, resolve: async p => {
      const r = await p.request.get(`${base}/api/routes`)
      const d = await r.json().catch(() => ({}))
      const first = (d.products || d.routes || [])[0]
      const id = typeof first === 'string' ? first : first && (first.product || first.id)
      return id ? `/routes/${encodeURIComponent(id)}` : '/routes'
    }, full: true },
  { name: 'slate',     path: '/slate', full: true },
  { name: 'results',   path: '/results', full: true },
  { name: 'topology',  path: '/topology', full: true },
]

const b = await chromium.launch()
const ctx = await b.newContext({ viewport: { width: W, height: H }, deviceScaleFactor: 2 })

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
  // The assistant's greeting bubble types itself in and would be caught
  // mid-word in every image.
  const hush = await p.$('.bubble-close, .rail-toggle')
  if (hush) { await hush.click().catch(() => {}) }
  await p.waitForTimeout(2500)
  if (s.prep) await s.prep(p)
  const file = path.join(OUT, `${s.name}.png`)
  await p.screenshot({ path: file, fullPage: !!s.full })
  console.log(`  ${s.name.padEnd(18)} ${target}`)
}

console.log(errs.length ? `\npage errors:\n  ${errs.join('\n  ')}` : '\nno page errors')
await b.close()
