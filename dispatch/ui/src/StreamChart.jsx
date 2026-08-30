import { useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import { M, view, yTicks, points, indexAt, xTickIndices } from './stream_geom.js'

// A strip-recorder line chart for the live rolling windows.
//
// Written by hand rather than reached for off the shelf because of how the
// data arrives: one sample at a time, into a fixed-length window. A charting
// library redraws the whole series on every sample and animates each vertex
// from its old position to its new one, so an arriving point makes every point
// on screen wobble -- the series appears to breathe in place instead of
// scrolling. What people actually read from a live monitor is horizontal
// motion, and a wobble reads as data changing when nothing changed but the
// window.
//
// So: geometry pins the newest sample to the right edge with a fixed slot per
// sample (stream_geom.js), and the one thing that animates is a single
// translate of the plot group by exactly one slot. Every drawn point holds
// still relative to its neighbours; the whole strip walks left. Old samples
// slide under the y axis and the incoming one slides in from the right edge,
// both hidden by one clip rect.
//
// props:
//   data     array of samples, oldest first
//   cap      window size in samples; sets the slot width
//   xOf      sample -> x-axis label (a clock string)
//   series   [{ key, name, color, fmt? }]
//   height   plot height in px
//   slideMs  travel time for one slot; kept under the sample interval, or the
//            chart is still sliding when the next sample lands and stutters
export default function StreamChart({
  data, cap = 120, xOf = d => d.t, series, height = 240, slideMs = 450,
  yLabel, empty = 'sampling…',
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
  const v = useMemo(
    () => (w ? view(n, cap, w, height, series.map(s => data.map(d => d[s.key]))) : null),
    [data, n, cap, w, height, series],
  )

  // The slide. On a new sample the group starts one slot to the right (where
  // the series stood before the sample arrived, since everything shifted left
  // by one slot when it did) and transitions back to zero. Two rAFs, not one:
  // the offset must be committed to the DOM *without* a transition before the
  // transition to zero is armed, or the browser coalesces both into no motion.
  const [shift, setShift] = useState(0)
  const [gliding, setGliding] = useState(false)
  const lastN = useRef(n)
  // Layout, not passive: the offset has to be in the DOM before the browser
  // paints the new sample, or one frame shows the series already in its final
  // position and the slide starts with a visible jump backwards.
  useLayoutEffect(() => {
    if (!v || n === lastN.current) { lastN.current = n; return }
    const grew = n > lastN.current
    lastN.current = n
    // Only a rolling window slides. A reset (cleared history, tab switch back)
    // is a new chart, not a step, and should not fly in from the side.
    if (!grew || n < 2) return
    setGliding(false)
    setShift(v.slot)
    let f2 = 0
    const f1 = requestAnimationFrame(() => {
      f2 = requestAnimationFrame(() => { setGliding(true); setShift(0) })
    })
    return () => { cancelAnimationFrame(f1); cancelAnimationFrame(f2) }
  }, [n, v])

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

  const onMove = (e) => {
    if (!v) return
    const r = e.currentTarget.getBoundingClientRect()
    const i = indexAt(v, e.clientX - r.left)
    setHover(i == null ? null : { i, x: e.clientX - r.left, y: e.clientY - r.top })
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
              {xTickIndices(v).map(i => (
                <text key={i} x={v.x(i)} y={height - 4} textAnchor="middle"
                      fontSize="10" fill="#6b7280">{xOf(data[i])}</text>
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

            {yLabel && (
              <text x={12} y={M.t + v.ih / 2} fontSize="11" fill="#6b7280"
                    textAnchor="middle"
                    transform={`rotate(-90 12 ${M.t + v.ih / 2})`}>{yLabel}</text>
            )}
          </svg>

          {hover && (
            // Pinned to the sample's slot rather than the pointer, so the
            // readout travels with the chart instead of lagging behind it.
            <div className="sc-tip" style={{
              left: Math.min(Math.max(v.x(hover.i) + dx, 8), (w || 0) - 8),
              top: 4,
            }}>
              <div className="sc-tip-t">{xOf(data[hover.i])}</div>
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
