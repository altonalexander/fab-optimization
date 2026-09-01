import { useEffect, useState } from 'react'
import {
  queueCells, ringLayout, recentOut, shortId, remaining, progress,
  fmtCountdown, setupLabel,
} from './toolflow_geom.js'

// The flow through one tool, left to right: the lots waiting for it, the
// lot(s) on it now with the process time left, and the lots that just left.
//
// The queue is the station family's: a lot waits for a family, not a machine,
// and any tool in the family may take it, so every tool in Dielectric_BE_60
// draws the same nine circles. The box is this tool's alone.
export default function ToolFlow({ t }) {
  // A one-second tick for the countdown. The page polls every two seconds;
  // between polls the fab clock is extrapolated at playback speed, so the
  // timer runs smoothly instead of stepping.
  const [wall, setWall] = useState(() => Date.now() / 1000)
  const ticking = (t.running_lots || []).some(r => r.end != null) && !t.paused
  useEffect(() => {
    if (!ticking) return
    const iv = setInterval(() => setWall(Date.now() / 1000), 1000)
    return () => clearInterval(iv)
  }, [ticking])

  const clock = { t: t.sim_t, t_at: t.sim_t_at, speed: t.speed, paused: t.paused }
  const cells = queueCells(t.waiting, t.waiting_count)
  const running = t.running_lots || (t.running || []).map(lot => ({ lot }))
  const ring = ringLayout(running.length)
  const out = recentOut(t.recent_out)
  const setup = setupLabel(t.setup)

  return (
    <div className="toolflow">
      <div className="tf-col">
        <div className="tf-label">waiting <b>{t.waiting_count ?? '—'}</b></div>
        <div className="tf-grid" title="lots queued for this tool's station family, oldest first">
          {cells.map((c, i) => <QueueCell key={i} cell={c} />)}
        </div>
      </div>

      <div className="tf-arrow" aria-hidden>→</div>

      <div className="tf-col">
        <div className="tf-label">on tool <b>{running.length}</b></div>
        <div className={running.length ? 'tf-box' : 'tf-box tf-box-idle'}>
          {setup && <span className="tf-setup" title="current setup">{setup}</span>}
          {running.length === 0 && <span className="tf-idle">idle</span>}
          {ring.points.map((p, i) => (
            <RunningLot key={running[i].lot} meta={running[i]} clock={clock}
                        wall={wall} x={p.x} y={p.y} />
          ))}
          {ring.extra > 0 && <span className="tf-more">+{ring.extra}</span>}
        </div>
      </div>

      <div className="tf-arrow" aria-hidden>→</div>

      <div className="tf-col">
        <div className="tf-label">recently out</div>
        {out.shown.length === 0 ? (
          <div className="muted tf-none">none yet</div>
        ) : (
          <ul className="tf-out">
            {out.shown.map(r => (
              <li key={r.lot}>
                <code>{r.lot}</code>
                {r.day != null && <span className="muted"> · day {r.day.toFixed(2)}</span>}
              </li>
            ))}
          </ul>
        )}
        {out.extra > 0 && (
          <div className="muted tf-none">{out.shown.length + out.extra} recent lots out</div>
        )}
      </div>
    </div>
  )
}

function QueueCell({ cell }) {
  if (cell.kind === 'empty') return <span className="tf-cell tf-cell-empty" />
  if (cell.kind === 'more') {
    return <span className="tf-cell tf-cell-more" title={`${cell.n} more waiting`}>+{cell.n}</span>
  }
  const { short, hot } = shortId(cell.id)
  return (
    <span className={hot ? 'tf-cell tf-cell-hot' : 'tf-cell'} title={cell.id}>{short}</span>
  )
}

function RunningLot({ meta, clock, wall, x, y }) {
  const { short, hot } = shortId(meta.lot)
  const left = remaining(meta, clock, wall)
  const frac = progress(meta, clock, wall)
  // Progress as a conic ring around the lot: the countdown says how long,
  // the ring says how far, and both are visible from across the room.
  const ringStyle = frac == null ? undefined : {
    background: `conic-gradient(#15803d ${Math.round(frac * 360)}deg, #dcfce7 0)`,
  }
  return (
    <div className={hot ? 'tf-run tf-run-hot' : 'tf-run'}
         style={{ left: `${x * 100}%`, top: `${y * 100}%`, ...ringStyle }}
         title={`${meta.lot}${left != null ? ` · ${fmtCountdown(left)} left` : ''}`}>
      <div className="tf-run-in">
        <div className="tf-run-id">{short}</div>
        <div className="tf-run-left">{left == null ? '' : fmtCountdown(left)}</div>
      </div>
    </div>
  )
}
