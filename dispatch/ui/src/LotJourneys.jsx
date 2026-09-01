import { useEffect, useState } from 'react'
import { linkTo } from './router.js'
import { widths, famLabel, fmtProc, statusOf } from './journey_geom.js'
import { remaining, progress, fmtCountdown } from './toolflow_geom.js'

// Each lot of the cohort as a short supply chain: the two steps it has just
// left, the step it is at, and the two ahead, with box width proportional to
// the step's nominal process time and arrows carrying the flow left to right.
// A lot on a tool shows the tool and the time left on it, counting down.
//
// The chart above answers "how far along is the cohort"; this answers "where
// exactly is each lot standing, and what is next for it".
export default function LotJourneys({ lots, clock, routeHref }) {
  const [wall, setWall] = useState(() => Date.now() / 1000)
  const ticking = !clock?.paused && lots.some(l => l.journey?.end != null)
  useEffect(() => {
    if (!ticking) return
    const iv = setInterval(() => setWall(Date.now() / 1000), 1000)
    return () => clearInterval(iv)
  }, [ticking])

  const rows = lots.filter(l => l.journey)
  if (!rows.length) return null
  const part = rows[0].part

  return (
    <div className="journeys">
      <div className="journeys-head">
        <h4>Lot journeys</h4>
        <span className="muted">
          last two steps → current → next two; box width is the step's nominal process time
        </span>
        {routeHref && part && (
          <a className="link" href={routeHref(part)}>full recipe for {part} →</a>
        )}
      </div>
      {rows.map(l => <Journey key={l.lot} lot={l} clock={clock} wall={wall} />)}
    </div>
  )
}

function Journey({ lot, clock, wall }) {
  const j = lot.journey
  const w = widths(j.steps)
  const status = statusOf(j)
  const left = j.tool ? remaining(j, clock, wall) : null
  const frac = j.tool ? progress(j, clock, wall) : null
  return (
    <div className="journey">
      <div className="journey-lot">
        <code>{lot.lot}</code>
        {lot.hot && <span className="chip chip-bad">hot</span>}
        <span className="muted">step {Math.min(j.idx + 1, j.n)} / {j.n} · {status}</span>
      </div>
      <div className="journey-strip">
        {j.steps.map((s, i) => {
          const cur = s.pos === 0
          const past = s.pos < 0
          const href = cur && j.tool
            ? linkTo(['tools', j.tool])
            : linkTo('/tools', { type: s.fam })
          const cls = 'jstep' + (cur ? ' jstep-cur' : past ? ' jstep-past' : ' jstep-next')
                    + (cur && j.tool ? ' jstep-on' : '')
          const title = `${s.step} · ${s.fam} · ${fmtProc(s.proc_s)} nominal`
                      + (s.bmax > 1 ? ` · batch up to ${s.bmax}` : '')
                      + (s.setup ? ` · setup ${s.setup}` : '')
          return (
            <span key={s.i} className="jseg" style={{ flexGrow: w[i], flexBasis: 0 }}>
              {i > 0 && <span className="jarrow" aria-hidden>→</span>}
              <a className={cls} href={href} title={title}
                 style={cur && frac != null
                   ? { background: `linear-gradient(90deg, #bbf7d0 ${Math.round(frac * 100)}%, #f0fdf4 0)` }
                   : undefined}>
                <span className="jstep-fam">{famLabel(s.fam)}</span>
                <span className="jstep-sub">
                  {cur && j.tool
                    ? <>{j.tool}{left != null && <b> · {fmtCountdown(left)} left</b>}</>
                    : cur && status === 'waiting'
                      ? 'waiting for a tool'
                      : fmtProc(s.proc_s)}
                </span>
                {(s.bmax > 1 || s.setup) && (
                  <span className="jstep-tags">
                    {s.bmax > 1 && <span className="chip chip-batch">batch</span>}
                    {s.setup && <span className="chip chip-setup">setup</span>}
                  </span>
                )}
              </a>
            </span>
          )
        })}
        {j.idx >= j.n && <span className="muted jdone">route complete</span>}
      </div>
    </div>
  )
}
