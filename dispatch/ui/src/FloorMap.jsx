import { useEffect, useMemo, useRef, useState } from 'react'

// ---------------------------------------------------------------------------
// Cleanroom floor map. 12 bays x 8 segments, 96 cells.
//
// Three layers, in z-order, and they are kept apart deliberately:
//   geometry    cell rects, dividers, track, labels -- rendered once, memoized
//   state       fills and WIP badges -- the only layer that re-renders on tick
//   interaction hover, selection, tooltip -- local state, never touched by data
// Mixing them is what makes fab maps feel sluggish: a WIP tick would repaint
// 96 rects and the track paths for no reason.
//
// The layout is SYNTHETIC -- SMT2020 has no floorplan -- and the map says so.
// ---------------------------------------------------------------------------

const CELL_W = 44, CELL_H = 38, PAD = 60

// One transform, used by rects, labels, badges, hover ring and tooltip anchor.
// Computing this inline in JSX is how those drift apart.
export const cellRect = (bay, seg) => ({
  x: PAD + bay * CELL_W,
  y: PAD + seg * CELL_H,
  w: CELL_W - 2,
  h: CELL_H - 2,
})

const BAYS = 12, SEGS = 8
const VB_W = PAD * 2 + BAYS * CELL_W
const VB_H = PAD * 2 + SEGS * CELL_H

// Not schedulable: no tools are ever placed here, so they render as structure
// rather than as cells that merely happen to be empty.
const STRUCTURAL = new Set(['STK'])

function Geometry({ layout, zoneColor }) {
  return (
    <g>
      {layout.cells.map(c => {
        const r = cellRect(c.bay, c.seg)
        const structural = STRUCTURAL.has(c.zone)
        return (
          <rect key={`g${c.bay}-${c.seg}`} x={r.x} y={r.y} width={r.w} height={r.h}
                rx={3}
                fill={structural ? '#f3f4f6' : '#fff'}
                stroke={structural ? '#e5e7eb' : zoneColor[c.zone] || '#d1d5db'}
                strokeWidth={structural ? 1 : 1.5}
                opacity={structural ? 1 : 0.9} />
        )
      })}
      {Array.from({ length: BAYS }, (_, b) => (
        <text key={`bx${b}`} x={cellRect(b, 0).x + CELL_W / 2 - 1} y={PAD - 12}
              textAnchor="middle" fontSize={10} fill="#6b7280">
          {String(b).padStart(2, '0')}
        </text>
      ))}
      {Array.from({ length: SEGS }, (_, s) => (
        <text key={`sy${s}`} x={PAD - 12} y={cellRect(0, s).y + CELL_H / 2}
              textAnchor="end" dominantBaseline="middle" fontSize={10} fill="#6b7280">
          S{s}
        </text>
      ))}
      {/* S0 and S7 are the interbay backbone, not a loop drawn outside. */}
      {[0, SEGS - 1].map(s => (
        <text key={`tr${s}`} x={VB_W - PAD + 8} y={cellRect(0, s).y + CELL_H / 2}
              dominantBaseline="middle" fontSize={9} fill="#9ca3af">interbay</text>
      ))}
    </g>
  )
}

const MemoGeometry = /* @__PURE__ */ (() => {
  let cache = null
  return function Cached(props) {
    // The geometry never changes after the first fetch, so memoise on identity.
    if (!cache || cache.layout !== props.layout) {
      cache = { layout: props.layout, el: <Geometry {...props} /> }
    }
    return cache.el
  }
})()

// sel ("bay,seg") and heat are lifted into the URL by App, so a specific bay
// with the heatmap on is a link rather than a set of clicks to describe.
export default function FloorMap({ onOpenTool, sel, onSel, heat, onHeat }) {
  const [layout, setLayout] = useState(null)
  const [state, setState] = useState(null)
  const setSel = onSel
  const [hover, setHover] = useState(null)
  const setHeat = onHeat
  const [err, setErr] = useState(null)

  useEffect(() => {
    let live = true
    fetch('/api/layout').then(r => r.json())
      .then(j => { if (!live) return; j.error ? setErr(j.error) : setLayout(j) })
      .catch(e => live && setErr(String(e)))
    return () => { live = false }
  }, [])

  useEffect(() => {
    let live = true
    const load = () => fetch('/api/layout/state').then(r => r.json())
      .then(j => live && setState(j)).catch(() => {})
    load()
    // The engine decides in microseconds; the map needs a couple of Hz. The
    // fast path must never be back-pressured by a dashboard.
    const iv = setInterval(load, 2000)
    return () => { live = false; clearInterval(iv) }
  }, [])

  const zoneColor = useMemo(() => {
    const m = {}
    for (const z of layout?.zones || []) m[z.id] = z.color
    return m
  }, [layout])

  const zoneLabel = useMemo(() => {
    const m = {}
    for (const z of layout?.zones || []) m[z.id] = z.label
    return m
  }, [layout])

  const cellState = useMemo(() => {
    const m = {}
    for (const c of state?.cells || []) m[`${c.bay},${c.seg}`] = c
    return m
  }, [state])

  const cellZone = useMemo(() => {
    const m = {}
    for (const c of layout?.cells || []) m[`${c.bay},${c.seg}`] = c.zone
    return m
  }, [layout])

  const maxWip = useMemo(() => {
    let m = 0
    for (const c of state?.cells || []) m = Math.max(m, c.wip || 0)
    return m || 1
  }, [state])

  if (err) return <div className="err">{err}</div>
  if (!layout) return <div className="muted">loading floorplan…</div>

  const selCell = sel ? cellState[sel] : null
  const selTools = sel ? (layout.cell_tools[sel] || []) : []

  return (
    <div className="floor-wrap">
      <div className="floor-main">
        <div className="floor-head">
          <div>
            {layout.synthetic && (
              <span className="tag tag-warn" title={layout.note}>synthetic layout</span>
            )}
            <span className="muted" style={{ marginLeft: 8 }}>
              {layout.envelope_m[0]}m × {layout.envelope_m[1]}m ·
              {' '}{layout.placed} tools placed
              {layout.unplaced.length > 0 &&
                <span className="danger"> · {layout.unplaced.length} unplaced</span>}
            </span>
          </div>
          <label className="heat-toggle">
            <input type="checkbox" checked={heat} onChange={e => setHeat(e.target.checked)} />
            {' '}WIP heatmap
          </label>
        </div>

        <svg viewBox={`0 0 ${VB_W} ${VB_H}`} width="100%" className="floor-svg"
             role="group" aria-label="Cleanroom floor map">
          <defs>
            {/* Down state is hatched as well as coloured, so it survives
                colourblindness and a projector. Never colour alone. */}
            <pattern id="downHatch" width="6" height="6" patternUnits="userSpaceOnUse"
                     patternTransform="rotate(45)">
              <rect width="6" height="6" fill="#fef2f2" />
              <line x1="0" y1="0" x2="0" y2="6" stroke="#b91c1c" strokeWidth="2" />
            </pattern>
          </defs>

          <MemoGeometry layout={layout} zoneColor={zoneColor} />

          {/* state layer */}
          <g>
            {layout.cells.map(c => {
              const key = `${c.bay},${c.seg}`
              const st = cellState[key]
              if (!st || !st.tools) return null
              const r = cellRect(c.bay, c.seg)
              const base = zoneColor[c.zone] || '#9ca3af'
              const frac = heat ? Math.min(1, (st.wip || 0) / maxWip) : 0.18
              return (
                <g key={`s${key}`}>
                  <rect x={r.x} y={r.y} width={r.w} height={r.h} rx={3}
                        fill={base} opacity={heat ? 0.12 + frac * 0.78 : 0.18} />
                  {st.down > 0 && (
                    <rect x={r.x} y={r.y} width={r.w} height={6} rx={2}
                          fill="url(#downHatch)" />
                  )}
                  <text x={r.x + r.w / 2} y={r.y + r.h / 2 - 2} textAnchor="middle"
                        fontSize={9} fill="#374151" fontWeight="600">{c.zone}</text>
                  {!heat && (
                    <text x={r.x + r.w - 3} y={r.y + r.h - 4} textAnchor="end"
                          fontSize={9} fill="#6b7280">{st.wip}</text>
                  )}
                </g>
              )
            })}
          </g>

          {/* interaction layer */}
          <g>
            {layout.cells.map(c => {
              const key = `${c.bay},${c.seg}`
              const st = cellState[key]
              const r = cellRect(c.bay, c.seg)
              const has = !!(st && st.tools)
              const open = () => has && setSel(sel === key ? null : key)
              return (
                <g key={`i${key}`}
                   role="button" tabIndex={has ? 0 : -1}
                   aria-label={has
                     ? `${zoneLabel[c.zone] || c.zone} bay ${c.bay} segment ${c.seg}, `
                       + `${st.tools} tools, ${st.wip} lots waiting`
                       + (st.down ? `, ${st.down} down` : '')
                     : `${c.zone} bay ${c.bay} segment ${c.seg}, no tools`}
                   onClick={open}
                   onKeyDown={e => {
                     // Fab floor terminals frequently have no mouse.
                     if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); open() }
                   }}
                   onMouseEnter={() => setHover(key)}
                   onMouseLeave={() => setHover(h => (h === key ? null : h))}
                   style={{ cursor: has ? 'pointer' : 'default' }}>
                  <rect x={r.x} y={r.y} width={r.w} height={r.h} rx={3} fill="transparent" />
                  {hover === key && has && (
                    <rect x={r.x} y={r.y} width={r.w} height={r.h} rx={3}
                          fill="none" stroke="#111827" strokeWidth={1.5} />
                  )}
                  {sel === key && (
                    <rect x={r.x - 2} y={r.y - 2} width={r.w + 4} height={r.h + 4} rx={4}
                          fill="none" stroke="#111827" strokeWidth={2.5} />
                  )}
                </g>
              )
            })}
          </g>
        </svg>

        <div className="floor-legend">
          {(layout.zones || []).filter(z => !STRUCTURAL.has(z.id)).map(z => (
            <span key={z.id} className="lg">
              <i style={{ background: z.color }} />{z.id} <span className="muted">{z.label}</span>
            </span>
          ))}
          <span className="lg"><i className="lg-hatch" />down</span>
        </div>

        <DelayStrip />
      </div>

      {sel && selCell && (
        // Side panel, not a modal: the operator needs to keep watching the map.
        <aside className="floor-panel">
          <div className="rail-head">
            <h4>bay {selCell.bay} · seg {selCell.seg}</h4>
            <button className="rail-toggle" onClick={() => setSel(null)}>×</button>
          </div>
          <div className="muted" style={{ marginBottom: 8 }}>
            {zoneLabel[cellZone[sel]] || cellZone[sel]}
          </div>
          <div className="stats-row">
            <Mini label="tools" value={selCell.tools} />
            <Mini label="wip" value={selCell.wip} />
            <Mini label="running" value={selCell.running} />
            <Mini label="down" value={selCell.down} />
          </div>
          <h5>Tools</h5>
          <div className="floor-tools">
            {selTools.map(t => (
              <button key={t} className="tcard" onClick={() => onOpenTool(t)}>
                <div className="tcard-id">{t}</div>
              </button>
            ))}
          </div>
        </aside>
      )}
    </div>
  )
}

function Mini({ label, value }) {
  return (
    <div className="stat">
      <div className="stat-label">{label}</div>
      <div className="stat-value">{value}</div>
    </div>
  )
}

function DelayStrip() {
  const [rows, setRows] = useState([])
  useEffect(() => {
    let live = true
    const load = () => fetch('/api/layout/state').then(r => r.json())
      .then(j => live && setRows(j.delays || [])).catch(() => {})
    load()
    const iv = setInterval(load, 4000)
    return () => { live = false; clearInterval(iv) }
  }, [])
  if (!rows.length) return null
  return (
    <div className="delay-strip">
      <h4>Queue-time delays</h4>
      <p className="muted">
        Not equipment and not on the floor: these are queue-time placeholders
        with no physical location. They are where lots wait, so they are worth
        watching — but giving them a bay would assert a position the model
        does not have.
      </p>
      <div className="delay-rows">
        {rows.map(d => (
          <div key={d.group} className="delay-row">
            <strong>{d.group}</strong>
            <span className="muted">{d.tools} positions</span>
            <span>{d.dispatches.toLocaleString()} dispatches</span>
            <span>wip {d.wip}</span>
          </div>
        ))}
      </div>
    </div>
  )
}
