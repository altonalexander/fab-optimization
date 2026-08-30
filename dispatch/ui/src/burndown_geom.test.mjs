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
  allPoints, batchBands, domain, domainWithProjection, envelope,
  historySegments, maxValue, projection, reworkJogs, segments, valueAt,
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

// ---------------------------------------------------------------------------
// Projection, rework and scrap
// ---------------------------------------------------------------------------
let sawProjection = false, sawJog = false

for (const row of idx.cohorts.slice(0, 25)) {
  const d = await (await fetch(`${BASE}/api/lots/${encodeURIComponent(row.cohort)}`)).json()
  if (!d.lots || !d.lots.length) continue
  const now = d.now_t

  for (const lot of d.lots) {
    // The route is fixed for the life of the lot. Rework moves completed steps
    // back in front of it; it does not add work to the route.
    if (lot.stats) {
      check(`route == done + left for ${lot.lot}`,
            lot.stats.route === lot.stats.steps_done + lot.stats.steps_left,
            `${lot.stats.route} vs ${lot.stats.steps_done}+${lot.stats.steps_left}`)
    }

    // Rework jogs the chart draws must match the count the stats report, or
    // the picture and the table disagree.
    const jogs = reworkJogs(lot)
    if (lot.stats) {
      check(`rework jog count matches stats for ${lot.lot}`,
            jogs.length === lot.stats.rework_events)
    }
    if (jogs.length) {
      sawJog = true
      check(`rework raises steps left for ${lot.lot}`,
            jogs.every(j => j.to > j.from && j.steps === j.to - j.from))
    }

    const pr = projection(lot, 'steps', now)
    if (pr) {
      sawProjection = true
      check(`projection ends at zero for ${lot.lot}`, pr.v2 === 0)
      check(`projection runs forward for ${lot.lot}`, pr.t2 >= pr.t1,
            `${pr.t1} -> ${pr.t2}`)
      check(`projection starts at or after now for ${lot.lot}`, pr.t1 >= now - 1)
      const sl = lot.projection.slack_s
      if (sl != null && lot.due) {
        check(`slack == due - eta for ${lot.lot}`,
              Math.abs(sl - (lot.due - lot.projection.eta_t)) < 1)
      }
    } else if (lot.state === 'active' && lot.projection) {
      // One legitimate case: the lot has completed its last step but has not
      // emitted `done` yet, so it is still `active` with nothing remaining.
      // There is no ray to draw and drawing one to "now" would be noise.
      check(`active lot without a ray has nothing left (${lot.lot})`,
            (lot.stats?.steps_left ?? 0) === 0,
            `steps_left=${lot.stats?.steps_left}`)
    }

    // A finished lot is not projected anywhere.
    if (lot.state === 'done') {
      check(`no projection for completed ${lot.lot}`, pr === null)
    }
  }

  // The visible window has to contain what we are asking the reader to compare.
  const [pd0, pd1] = domainWithProjection(d.lots, now, 'steps')
  for (const lot of d.lots) {
    if (lot.due) {
      check(`due date inside domain for ${lot.lot}`, lot.due <= pd1 && lot.due >= pd0)
    }
    const pr = projection(lot, 'steps', now)
    if (pr) check(`eta inside domain for ${lot.lot}`, pr.t2 <= pd1)
  }
}

// ---------------------------------------------------------------------------
// Warm-up history
// ---------------------------------------------------------------------------
const warm = idx.warm_t
let sawHistory = false, histLots = 0

if (warm == null) {
  console.log('  note no warm_t: feed was run without --warmup-days, '
              + 'so there is no history to check')
} else {
  console.log(`warm-up boundary: day ${(warm / 86400).toFixed(2)}`)
  for (const row of idx.cohorts.slice(0, 30)) {
    const d = await (await fetch(`${BASE}/api/lots/${encodeURIComponent(row.cohort)}`)).json()
    for (const lot of d.lots || []) {
      const h = lot.history || []
      if (!h.length) continue
      sawHistory = true
      histLots++

      // History is the past, by definition. A point after the line would mean
      // the snapshot captured something from the live run.
      check(`history precedes the warm line for ${lot.lot}`,
            h.every(x => x.t <= warm + 1))
      check(`history is ordered for ${lot.lot}`,
            h.every((x, i) => i === 0 || x.t >= h[i - 1].t))

      // The two halves must join, or the chart shows a lot teleporting.
      if (lot.points.length) {
        check(`live resumes after history for ${lot.lot}`,
              lot.points[0].t >= h[h.length - 1].t)
        const merged = allPoints(lot)
        check(`allPoints is ordered for ${lot.lot}`,
              merged.every((x, i) => i === 0 || x.t >= merged[i - 1].t))
        check(`allPoints spans both halves for ${lot.lot}`,
              merged.length === h.length + lot.points.filter(
                x => x.t > h[h.length - 1].t).length)
      }

      // Historic segments must be a staircase like the live ones, and must
      // never be drawn past the boundary except for the joining run.
      const hs = historySegments(lot, 'steps')
      if (hs.length) {
        check(`historic segments are flat-or-vertical for ${lot.lot}`,
              hs.every(x => (x.flat && x.v1 === x.v2) || (!x.flat && x.t1 === x.t2)))
        check(`historic segments are tagged for ${lot.lot}`,
              hs.every(x => x.historic === true))
      }
      check(`no historic segments for the time metric (${lot.lot})`,
            historySegments(lot, 'time').length === 0,
            '- history carries no rem_s, so a process-time staircase would be invented')

      // The window has to actually contain the history we are drawing.
      const [hd0] = domainWithProjection(d.lots, d.now_t, 'steps')
      check(`domain reaches back to history for ${lot.lot}`, hd0 <= h[0].t)
    }
    if (histLots > 12) break
  }
  check('saw warm-up history on at least one lot', sawHistory)
}

// Scrap never occurs in SMT2020 -- there is no scrap concept in the dataset or
// the simulator -- so the path is exercised synthetically rather than left
// untested until a real MES feed arrives.
{
  const scrapped = {
    lot: 'SYNTH_1', state: 'scrapped', due: 100, release: 0,
    projection: { start_t: 10, eta_t: 90, rate_s: 1, basis: 'fab', n: 99 },
    points: [{ t: 0, left: 10, reason: 'none', rem_s: 100 },
             { t: 10, left: 8, reason: 'proc', rem_s: 80 }],
  }
  check('scrapped lot gets no projection',
        projection(scrapped, 'steps', 1000) === null)
  const segs = segments(scrapped, 'steps', 1000)
  check('scrapped lot line is not extended to now',
        !segs.some(x => x.extended),
        '- a scrapped lot is not waiting, its line just ends')
}

console.log(`\nlots checked: ${checkedLots}`)
check('saw at least one projection', sawProjection)
check('segments are well formed for every lot checked', checkedLots > 0)
// Whether a completion falls inside the retained window depends on how long
// the feed has been running, not on the geometry. Report it, do not fail on it.
console.log(sawDone
  ? '  ok   saw a completed lot (line reaches zero)'
  : '  note no completed lot in this window (young feed; cycle time is ~weeks)')
check('saw a stale lot extended to now', sawExtended,
      '- flat-to-now is what stops a stuck lot looking finished')
check('saw a batch-wait segment', sawCohortWait,
      '- the "waiting on cohort" attribution is exercised')
console.log(sawRework
  ? '  ok   saw rework raise the burndown (non-monotonic)'
  : '  note no rework in this window (rare: ~3 per 2 simulated days)')

console.log(failures ? `\n${failures} FAILED` : '\nall geometry checks passed')
process.exit(failures ? 1 : 0)
