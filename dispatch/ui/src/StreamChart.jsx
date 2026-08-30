import { useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import {
  M, view, timeView, yTicks, points, indexAt, xTickIndices, fmtSimTime, travel,
} from './stream_geom.js'

// A strip-recorder line chart for the live rolling windows.
//
// Written by hand rather than reached for off the shelf because of how the
// data arrives: one sample at a time, into a fixed-length window. A charting
// library redraws the whole series on every sample and tweens each vertex from
// its old position to its new one, so an arriving point makes every point on
// screen wobble -- the series appears to breathe in place instead of
// scrolling. What people actually read from a live monitor is horizontal
// motion, and a wobble reads as data changing when nothing changed but the
// window.
//
// So: the geometry pins the newest sample to the right edge (stream_geom.js),
// and the one thing that animates is a single translate of the plot group by
// however far the series just moved. Every drawn point holds still relative to
// its neighbours; the whole strip walks left. Old samples slide under the y
// axis and the incoming one slides in from the right edge, both hidden by one
// clip rect.
//
// Two x modes, because the two charts measure different clocks:
//   index -- one slot per sample, for wall-clock measurements like the event
//            rate, where the browser's clock IS the subject.
//   time  -- x is simulated fab time, for fab data like WIP, which must not be
//            plotted against wall clock while the feed replays at 1x to 400x.
//            Distance is elapsed fab time, so a pause moves the chart by
//            nothing and a speed change is visible as spacing rather than as a
//            silent rescale. See timeView.
//
// props:
//   data     array of samples, oldest first
//   cap      window size in samples
//   xMode    'index' | 'time'
//   tOf      sample -> simulated seconds (time mode)
//   span     sim seconds visible across the plot (time mode; see spanFor)
//   xOf      sample -> x label (index mode; time mode labels the fab clock)
//   series   [{ key, name, color, fmt? }]
//   marks    [{ t, label }] rules in sim time, e.g. where playback speed changed
//   frozen   text to show when the clock is not advancing (pause)
//   slideMs  travel time for one step; kept under the sample interval, or the
//            chart is still sliding when the next sample lands and stutters
export default function StreamChart({
  data, cap = 120, xMode = 'index', tOf = d => d.simT, span = 0,
  xOf = d => d.t, series, height = 240, slideMs = 450, yLabel,
  marks = [], frozen = null, empty = 'sampling…',
}) {
  const box = useRef(null)
  const [w, setW] = useState(0)
  const [hover, setHover] = useState(null)

  useLayoutEffect(() => {
    const el = box.current
    if (!el) return
    const ro = new ResizeObserver(([e]) => setW(e.contentRect.width))
    ro.observe(el)
    setW(el.getBoundingClientRect().width)
    return () => ro.disconnect()
  }, [])

  const n = data.length
  const timed = xMode === 'time'
  const v = useMemo(() => {
    if (!w) return null
    const cols = series.map(s => data.map(d => d[s.key]))
    return timed
      ? timeView(data.map(tOf), span, w, height, cols)
      : view(n, cap, w, height, cols)
  }, [data, n, cap, w, height, series, timed, tOf, span])

  // How far the series has just travelled, in its own units: one slot per
  // sample by index, or the elapsed fab time by clock. In time mode a paused
  // feed advances this by zero, so the chart holds still on its own -- there is
  // no special case for pause anywhere in here.
  const at = timed ? (n ? tOf(data[n - 1]) : 0) : n
  const prev = useRef({ at, span })
  const [shift, setShift] = useState(0)
  const [gliding, setGliding] = useState(false)

  // Layout, not passive: the offset has to be in the DOM before the browser
  // paints the new sample, or one frame shows the series already in its final
  // position and the slide starts with a visible jump backwards. Two rAFs, not
  // one: the offset must be committed *without* a transition before the
  // transition to zero is armed, or the browser coalesces both into no motion.
  useLayoutEffect(() => {
    const was = prev.current
    prev.current = { at, span }
    // travel() decides both how far and whether to animate at all; a speed
    // change, a reset or a reconnect snap instead of sliding. See stream_geom.
    const travelled = travel(was, { at, span }, v, timed)
    if (!travelled) return
    setGliding(false)
    setShift(travelled)
    let f2 = 0
    const f1 = requestAnimationFrame(() => {
      f2 = requestAnimationFrame(() => { setGliding(true); setShift(0) })
    })
    return () => { cancelAnimationFrame(f1); cancelAnimationFrame(f2) }
  }, [at, span, n, v, timed])

  // Motion is decoration here: the chart is fully readable without it, so an
  // OS-level request for less of it is honoured rather than reinterpreted.
  const [still, setStill] = useState(false)
  useEffect(() => {
    const mq = window.matchMedia('(prefers-reduced-motion: reduce)')
    const on = () => setStill(mq.matches)
    on()
    mq.addEventListener('change', on)
    return () => mq.removeEventListener('change', on)
  }, [])

  const clipId = useRef(`sc-${Math.random().toString(36).slice(2)}`).current
  const dx = still ? 0 : shift
  const label = i => (timed ? fmtSimTime(tOf(data[i])) : xOf(data[i]))

  const onMove = (e) => {
    if (!v) return
    const r = e.currentTarget.getBoundingClientRect()
    const i = indexAt(v, e.clientX - r.left)
    setHover(i == null ? null : { i })
  }

  return (
    <div className="sc" ref={box}>
      <div className="sc-legend">
        {series.map(s => (
          <span key={s.key}>
            <i style={{ background: s.color }} />{s.name}
          </span>
        ))}
      </div>

      {n < 2 || !v ? (
        <div className="sc-empty muted" style={{ height }}>{empty}</div>
      ) : (
        <div className="sc-plot" style={{ height }}>
          <svg width="100%" height={height} onMouseMove={onMove}
               onMouseLeave={() => setHover(null)} role="img"
               aria-label={series.map(s => s.name).join(' and ') + ' over time'}>
            <defs>
              {/* One clip for everything that moves, covering the plot and the
                  x-axis band: it is what hides a sample entering at the right
                  edge and one leaving under the y axis. */}
              <clipPath id={clipId}>
                <rect x={M.l} y={0} width={v.iw} height={height} />
              </clipPath>
            </defs>

            {yTicks(v).map(t => (
              <g key={t}>
                <line x1={M.l} x2={M.l + v.iw} y1={v.y(t)} y2={v.y(t)}
                      stroke="#e5e7eb" />
                <text x={M.l - 6} y={v.y(t) + 4} textAnchor="end" fontSize="11"
                      fill="#6b7280">{fmtTick(t)}</text>
              </g>
            ))}

            <g clipPath={`url(#${clipId})`}
               style={{
                 transform: `translateX(${dx}px)`,
                 transition: gliding && !still
                   ? `transform ${slideMs}ms linear` : 'none',
               }}>
              {/* Where playback speed changed. Drawn because a pixel either
                  side of it carries a different amount of fab time, and an
                  unmarked change is exactly the distortion this chart exists
                  to avoid. */}
              {timed && marks.map(m => v.xAt(m.t) > M.l && (
                <g key={m.t}>
                  <line x1={v.xAt(m.t)} x2={v.xAt(m.t)} y1={M.t} y2={M.t + v.ih}
                        stroke="#c2410c" strokeWidth="1" strokeDasharray="2 3" />
                  <text x={v.xAt(m.t) + 3} y={M.t + 9} fontSize="9"
                        fill="#c2410c">{m.label}</text>
                </g>
              ))}
              {xTickIndices(v).map(i => (
                <text key={i} x={v.x(i)} y={height - 4} textAnchor="middle"
                      fontSize="10" fill="#6b7280">{label(i)}</text>
              ))}
              {series.map(s => (
                <polyline key={s.key} fill="none" stroke={s.color}
                          strokeWidth="2" strokeLinejoin="round"
                          strokeLinecap="round"
                          points={points(v, data.map(d => d[s.key]))} />
              ))}
              {hover && (
                <g>
                  <line x1={v.x(hover.i)} x2={v.x(hover.i)} y1={M.t}
                        y2={M.t + v.ih} stroke="#9ca3af" strokeDasharray="3 3" />
                  {series.map(s => Number.isFinite(data[hover.i][s.key]) && (
                    <circle key={s.key} r="3" cx={v.x(hover.i)}
                            cy={v.y(data[hover.i][s.key])} fill={s.color} />
                  ))}
                </g>
              )}
            </g>

            {/* now: the newest sample's position, and the boundary of what is
                known. Drawn outside the sliding group so it stays put while the
                strip walks under it -- a "now" that slid left with the data
                would be marking a moment in the past. Everything to its right
                is the future, deliberately empty. */}
            <line x1={v.nowX} x2={v.nowX} y1={M.t} y2={M.t + v.ih}
                  stroke="#111827" strokeDasharray="3 3" />
            <text x={v.nowX + 4} y={M.t + 10} fontSize="10" fill="#111827">now</text>

            {yLabel && (
              <text x={12} y={M.t + v.ih / 2} fontSize="11" fill="#6b7280"
                    textAnchor="middle"
                    transform={`rotate(-90 12 ${M.t + v.ih / 2})`}>{yLabel}</text>
            )}
          </svg>

          {/* A frozen chart is the honest rendering of a paused fab, but on its
              own it is indistinguishable from a dead stream -- so it says which
              one it is rather than leaving the reader to guess. */}
          {frozen && <div className="sc-frozen">{frozen}</div>}

          {hover && (
            // Pinned to the sample rather than to the pointer, so the readout
            // travels with the chart instead of lagging behind it.
            <div className="sc-tip" style={{
              left: Math.min(Math.max(v.x(hover.i) + dx, 8), (w || 0) - 8),
              top: 4,
            }}>
              <div className="sc-tip-t">{label(hover.i)}</div>
              {series.map(s => (
                <div key={s.key}>
                  <i style={{ background: s.color }} />{s.name}
                  <b>{fmtVal(data[hover.i][s.key], s.fmt)}</b>
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  )
}

const fmtTick = t => (t >= 1000 ? `${(t / 1000).toFixed(t % 1000 ? 1 : 0)}k`
  : Number.isInteger(t) ? String(t)
  // A sub-unit axis (event rate at idle) needs the decimals the label would
  // otherwise round away, which would print four gridlines all reading "0".
  : t < 1 ? String(Number(t.toFixed(2))) : t.toFixed(1))

const fmtVal = (v, fmt) =>
  v == null || !Number.isFinite(v) ? '—' : fmt ? fmt(v) : String(v)
