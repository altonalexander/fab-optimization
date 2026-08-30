/**
 * Geometry checks for the cohort burndown, run against a live API.
 *
 *   node src/burndown_geom.test.mjs [base-url]
 *
 * Deliberately exercised on real simulator output rather than a fixture: the
 * properties worth defending here (non-monotonic burndown from rework, flat
 * runs attributed to a cause, stale lots extending to now) are properties of
 * SMT2020's behaviour, and a hand-written fixture would only assert that the
 * author's assumptions agree with themselves.
 */
import {
  batchBands, domain, envelope, maxValue, segments, valueAt,
} from './burndown_geom.js'

const BASE = process.argv[2] || 'http://localhost:8077'
let failures = 0
const check = (name, cond, detail = '') => {
  if (cond) { console.log(`  ok   ${name}`) }
  else { failures++; console.log(`  FAIL ${name} ${detail}`) }
}

const idx = await (await fetch(`${BASE}/api/lots?limit=60`)).json()
console.log(`cohorts: ${idx.total_cohorts}, lots tracked: ${idx.lots_tracked}, ` +
            `points ${idx.points_held}/${idx.points_cap}`)
check('index returns cohorts', idx.cohorts.length > 0)
check('cohorts are product-scoped',
      idx.cohorts.every(c => c.cohort.startsWith(c.part)),
      'a cohort whose lots cannot batch together would be meaningless')
check('spread is max-min', idx.cohorts.every(c => c.spread === c.max_left - c.min_left))
check('median lies within the band',
      idx.cohorts.every(c => c.med_left >= c.min_left && c.med_left <= c.max_left))

// Walk several cohorts so the checks see rework, completions and stalls.
let sawRework = false, sawCohortWait = false, sawExtended = false, sawDone = false
let checkedLots = 0

for (const row of idx.cohorts.slice(0, 25)) {
  const d = await (await fetch(`${BASE}/api/lots/${encodeURIComponent(row.cohort)}`)).json()
  if (!d.lots || !d.lots.length) continue
  const now = d.now_t

  for (const lot of d.lots) {
    checkedLots++
    const segs = segments(lot, 'steps', now)
    if (!segs.length) continue

    // time never runs backwards
    if (segs.some(s => s.t2 < s.t1)) {
      check(`time monotonic in ${lot.lot}`, false); break
    }
    // flat runs are flat; vertical moves are instantaneous
    if (segs.some(s => s.flat && s.v1 !== s.v2)) {
      check(`flat runs are level in ${lot.lot}`, false); break
    }
    if (segs.some(s => !s.flat && s.t1 !== s.t2)) {
      check(`drops are vertical in ${lot.lot}`, false); break
    }
    if (segs.some(s => s.v2 > s.v1 && !s.flat)) sawRework = true
    if (lot.points.some(p => p.reason === 'cohort')) sawCohortWait = true
    if (segs.some(s => s.extended)) sawExtended = true
    if (lot.state === 'done') sawDone = true

    // step-after lookup agrees with the raw points
    const p = lot.points[Math.floor(lot.points.length / 2)]
    if (valueAt(lot.points, p.t, 'steps') !== p.left) {
      check(`valueAt agrees with points for ${lot.lot}`, false)
    }
    if (valueAt(lot.points, lot.points[0].t - 1, 'steps') !== null) {
      check(`valueAt is null before release for ${lot.lot}`, false)
    }
  }

  const [d0, d1] = domain(d.lots, now)
  const env = envelope(d.lots, d0, d1, now, 'steps', 60)
  if (env.length) {
    check(`envelope ordered for ${row.cohort}`,
          env.every(e => e.min <= e.med && e.med <= e.max))
  }
  const bands = batchBands(d.lots)
  if (bands.length) {
    check(`batch bands within y-domain for ${row.cohort}`,
          bands.every(b => b >= 0 && b <= maxValue(d.lots, 'steps')))
  }
}

console.log(`\nlots checked: ${checkedLots}`)
check('segments are well formed for every lot checked', checkedLots > 0)
check('saw a completed lot (line reaches zero)', sawDone)
check('saw a stale lot extended to now', sawExtended,
      '- flat-to-now is what stops a stuck lot looking finished')
check('saw a batch-wait segment', sawCohortWait,
      '- the "waiting on cohort" attribution is exercised')
console.log(sawRework
  ? '  ok   saw rework raise the burndown (non-monotonic)'
  : '  note no rework in this window (rare: ~3 per 2 simulated days)')

console.log(failures ? `\n${failures} FAILED` : '\nall geometry checks passed')
process.exit(failures ? 1 : 0)
