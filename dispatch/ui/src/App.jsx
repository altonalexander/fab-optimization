import { useEffect, useMemo, useRef, useState } from 'react'
import ChatPanel from './ChatPanel.jsx'
import FloorMap from './FloorMap.jsx'
import CohortBurndown from './CohortBurndown.jsx'
import { RouteIndex, RouteProduct } from './RoutePages.jsx'
import { useRoute, linkTo, TABS } from './router.js'
import ToolAvailability from './ToolAvailability.jsx'
import StreamChart from './StreamChart.jsx'
import { spanFor, fmtSpan, fmtSimTime } from './stream_geom.js'

// ---------------------------------------------------------------------------
// ZONE 3 — enterprise. This app is READ-ONLY by construction: it talks only to
// the API, which consumes Kafka and has no write path to the dispatcher.
// Scenario runs execute against a cloned registry in the C++ planner.
// ---------------------------------------------------------------------------

const ZONE_COLORS = {
  equipment:  '#c2410c',
  realtime:   '#b91c1c',
  data:       '#1d4ed8',
  enterprise: '#15803d',
}

function useLiveState() {
  const [state, setState] = useState(null)
  const [feed, setFeed] = useState([])
  const [connected, setConnected] = useState(false)
  const [history, setHistory] = useState([])
  // Measured link metrics, not modelled ones: every number below is derived
  // from messages that actually arrived on this browser's SSE connection.
  const [link, setLink] = useState({
    eventRate: null,     // envelopes/s across the data zone, 10s window
    lagMs: null,         // api snapshot timestamp -> arrival at this browser
    heartbeatMs: null,   // observed interval between state frames
    rateHistory: [],     // sparkline of eventRate
    totalMsgs: 0,
  })
  const feedRef = useRef([])
  const marksRef = useRef([])          // arrival times of event/decision frames
  const lastStateRef = useRef(null)    // arrival time of the previous state frame

  useEffect(() => {
    let es
    const connect = () => {
      es = new EventSource('/api/stream')
      es.onopen = () => setConnected(true)
      es.onerror = () => { setConnected(false); es.close(); setTimeout(connect, 3000) }
      es.onmessage = (e) => {
        const msg = JSON.parse(e.data)
        const now = Date.now()
        if (msg.kind === 'state') {
          setState(msg.state)
          // The simulated clock rides on the frame it describes rather than
          // being polled alongside it: the WIP chart plots fab time, and a
          // clock read at some other moment would put the sample in the wrong
          // place. sim.t is null when nothing on the wire is sim-stamped, and
          // the chart falls back to wall clock rather than inventing one.
          const sim = msg.state.sim || {}
          setHistory(h => [...h.slice(-119), {
            t: new Date().toLocaleTimeString(),
            simT: sim.t,
            simAt: sim.t_at,
            speed: sim.speed,
            paused: !!sim.paused,
            ready: msg.state.ready,
            inFlight: msg.state.in_flight,
            completed: msg.state.completed,
          }])
          // Rate over a 10s trailing window. Short enough to react, long
          // enough that a single burst does not make the number meaningless.
          const marks = marksRef.current.filter(t => now - t < 10000)
          marksRef.current = marks
          const parsed = Date.parse(msg.state.ts)
          const lag = Number.isNaN(parsed) ? null : Math.max(0, now - parsed)
          const beat = lastStateRef.current ? now - lastStateRef.current : null
          lastStateRef.current = now
          setLink(l => {
            const rate = marks.length / 10
            return {
              ...l,
              eventRate: rate,
              lagMs: lag,
              heartbeatMs: beat,
              rateHistory: [...l.rateHistory.slice(-59), {
                t: new Date().toLocaleTimeString(), rate: Number(rate.toFixed(2)),
              }],
            }
          })
        } else if (msg.kind === 'event' || msg.kind === 'decision') {
          marksRef.current.push(now)
          setLink(l => ({ ...l, totalMsgs: l.totalMsgs + 1 }))
          feedRef.current = [msg, ...feedRef.current].slice(0, 60)
          setFeed(feedRef.current)
        }
      }
    }
    connect()
    // Seed immediately so the page is not blank before the first heartbeat.
    fetch('/api/state').then(r => r.json()).then(setState).catch(() => {})
    return () => es && es.close()
  }, [])

  return { state, feed, connected, history, link }
}

// `href` makes a tile a link to the view that explains it. A plain <a> rather
// than an onClick so the hash router, middle-click, and copy-link all keep
// working for free -- the tile becomes a real URL, not a click handler.
function Stat({ label, value, sub, accent, href, title }) {
  const body = (
    <>
      <div className="stat-label">{label}</div>
      <div className="stat-value" style={accent ? { color: accent } : undefined}>{value}</div>
      {sub && <div className="stat-sub">{sub}</div>}
    </>
  )
  if (!href) return <div className="stat">{body}</div>
  return <a className="stat stat-link" href={href} title={title}>{body}</a>
}

// Derives the flow graph from the boundary policy itself, so these counts
// cannot drift from zones.yaml: an edge is an entry under boundaries[].allowed,
// and a node is whatever that entry names (a zone, or the dual-homed service
// that mediates it). Source = emits but never receives, sink = the reverse,
// relay = both. Nothing here is hand-maintained.
function analyzeTopology(zones) {
  if (!zones || !zones.zones) return null
  const out = new Map(), inn = new Map()
  const touch = (m, k) => m.set(k, (m.get(k) || 0) + 1)
  const edges = []
  for (const b of zones.boundaries || []) {
    for (const a of b.allowed || []) {
      if (!a.from || !a.to) continue
      edges.push({ ...a, service: b.service })
      touch(out, a.from); touch(inn, a.to)
      if (!inn.has(a.from)) inn.set(a.from, 0)
      if (!out.has(a.to)) out.set(a.to, 0)
    }
  }
  const nodes = [...new Set([...out.keys(), ...inn.keys()])]
  const roleOf = (n) => {
    const o = out.get(n) || 0, i = inn.get(n) || 0
    if (o && !i) return 'source'
    if (i && !o) return 'sink'
    return 'relay'
  }
  const roles = Object.fromEntries(nodes.map(n => [n, roleOf(n)]))
  const protocols = new Set()
  for (const z of zones.zones) (z.protocols || []).forEach(p => protocols.add(p))
  const budget = zones.zones
    .map(z => z.latency_budget_ms)
    .filter(v => typeof v === 'number')
  return {
    edges,
    roles,
    sources: nodes.filter(n => roles[n] === 'source'),
    sinks: nodes.filter(n => roles[n] === 'sink'),
    relays: nodes.filter(n => roles[n] === 'relay'),
    dualHomed: (zones.boundaries || []).map(b => b.service),
    protocols: [...protocols],
    tightestBudgetMs: budget.length ? Math.min(...budget) : null,
  }
}

const fmtMs = (v) => v == null ? '—' : v < 10 ? v.toFixed(1) : Math.round(v)

function TopologyMetrics({ zones, state, link, connected }) {
  const g = useMemo(() => analyzeTopology(zones), [zones])
  if (!g) return null

  // The realtime zone's budget is a policy number; the lag we measure is the
  // enterprise-side mirror path (zone 2 -> 3 -> browser). They are different
  // paths, so they are labelled separately rather than compared.
  const lagAccent = link.lagMs != null && link.lagMs > 2000 ? '#b91c1c' : undefined

  return (
    <>
      <div className="stats-row">
        <Stat label="event throughput"
              value={link.eventRate == null ? '—' : link.eventRate.toFixed(1)}
              sub="envelopes/s · 10s window" />
        <Stat label="lot throughput"
              value={state ? state.throughput_lots_per_hour : '—'}
              sub="lots/hr · 120s window" />
        <Stat label="mirror lag" value={fmtMs(link.lagMs)}
              accent={lagAccent} sub="ms · zone 2→3→browser" />
        <Stat label="heartbeat" value={fmtMs(link.heartbeatMs)}
              sub="ms between state frames" />
        <Stat label="sources" value={g.sources.length}
              sub={g.sources.join(', ') || '—'} />
        <Stat label="sinks" value={g.sinks.length}
              sub={g.sinks.join(', ') || '—'} />
        <Stat label="relays" value={g.relays.length}
              sub={g.relays.join(', ') || '—'} />
        <Stat label="boundary crossings" value={g.edges.length}
              sub={`${g.dualHomed.length} dual-homed services`} />
        <Stat label="rt budget"
              value={g.tightestBudgetMs == null ? '—' : g.tightestBudgetMs}
              sub="ms · zone 1 policy, not measured" />
        <Stat label="frames seen" value={link.totalMsgs}
              accent={connected ? undefined : '#b91c1c'}
              sub={connected ? 'stream live' : 'stream down'} />
      </div>

      <section>
        <h3>Event rate</h3>
        {link.rateHistory.length < 2
          ? <div className="muted">sampling…</div>
          : (
            <StreamChart
              data={link.rateHistory} cap={60} height={160}
              series={[{ key: 'rate', name: 'envelopes/s', color: '#1d4ed8',
                         fmt: v => v.toFixed(2) }]} />
          )}
      </section>

      <section>
        <h3>Boundary crossings</h3>
        <table className="tbl">
          <thead>
            <tr><th>service</th><th>from</th><th>to</th><th>protocol</th>
                <th>port</th><th>mode</th></tr>
          </thead>
          <tbody>
            {g.edges.map((e, i) => (
              <tr key={i}>
                <td><code>{e.service}</code></td>
                <td>{e.from}</td>
                <td>{e.to}</td>
                <td>{e.proto}</td>
                <td>{e.port == null ? '—' : e.port}</td>
                <td>{e.mode || 'bidirectional'}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>
    </>
  )
}

// A picture of the same policy the table below states in words. Every rect,
// arrow and label is read out of zones.yaml — nothing here is hand-placed, so
// adding a zone or a boundary redraws the diagram instead of stale-ing it.
//
// Vertical position IS the trust gradient: zone 0 (hostile equipment) at the
// top, zone 3 (the only zone a human logs into) at the bottom. A dual-homed
// service is drawn straddling the line it is allowed to cross, which is the
// visual claim the whole design rests on: the only way down the page is
// through one of three reviewed processes.
function ZoneDiagram({ zones }) {
  if (!zones || !zones.zones) return <div className="muted">loading topology…</div>

  const BAND_H = 92, GAP = 104, X0 = 8, BAND_W = 516, W = 772
  const bands = zones.zones.map((z, i) => ({ z, y: 18 + i * (BAND_H + GAP) }))
  const H = 18 + bands.length * BAND_H + (bands.length - 1) * GAP + 14
  const byName = new Map(bands.map(b => [b.z.name, b]))
  const byId = (id) => bands.find(b => b.z.id === id)

  const boundaries = (zones.boundaries || []).map(b => {
    const ids = [...b.zones].sort((p, q) => p - q)
    const lo = byId(ids[0]), hi = byId(ids[ids.length - 1])
    const y = lo && hi ? (lo.y + BAND_H + hi.y) / 2 : 0
    return { ...b, y }
  })

  // A flow endpoint is either a zone (→ band centre) or the mediating service
  // itself (→ the boundary line). Drawing it that way is why the adapter's two
  // hops read as one descent from equipment to realtime rather than two
  // unrelated arrows.
  const yOf = (name, bnd) =>
    byName.has(name) ? byName.get(name).y + BAND_H / 2 : bnd.y

  // SVG <text> does not wrap, and the denied rules are written as prose in
  // zones.yaml, so they are broken into tspans here rather than truncated:
  // "no path exists from a browser to the dispatcher" is the sentence most
  // worth reading in the whole diagram.
  const wrap = (s, n) => {
    const lines = []
    let cur = ''
    for (const w of String(s).split(/\s+/)) {
      if (cur && (cur + ' ' + w).length > n) { lines.push(cur); cur = w }
      else cur = cur ? cur + ' ' + w : w
    }
    if (cur) lines.push(cur)
    return lines
  }

  return (
    <svg className="zone-diagram" viewBox={`0 0 ${W} ${H}`}
         role="img" aria-label="Network segmentation and boundary crossings">
      <defs>
        <marker id="zd-arrow" viewBox="0 0 10 10" refX="9" refY="5"
                markerWidth="6" markerHeight="6" orient="auto-start-reverse">
          <path d="M0 0 L10 5 L0 10 z" fill="#475569" />
        </marker>
      </defs>

      {boundaries.map((b, i) => (
        <g key={`b${i}`}>
          <line x1={X0} y1={b.y} x2={X0 + BAND_W} y2={b.y}
                stroke="#94a3b8" strokeWidth="1" strokeDasharray="5 4" />

          {(b.allowed || []).map((a, j) => {
            const x = X0 + 150 + j * 128
            const y1 = yOf(a.from, b), y2 = yOf(a.to, b)
            const down = y2 > y1
            const pad = 6
            return (
              <g key={`a${j}`}>
                <line x1={x} y1={y1 + (down ? pad : -pad)}
                      x2={x} y2={y2 + (down ? -pad : pad)}
                      stroke="#475569" strokeWidth="1.5"
                      markerEnd="url(#zd-arrow)" />
                <text x={x + 6} y={(y1 + y2) / 2 - 2} className="zd-flow">
                  {a.proto}
                </text>
                <text x={x + 6} y={(y1 + y2) / 2 + 10} className="zd-flow zd-dim">
                  {a.port == null ? (a.mode || '') : `:${a.port}`}
                  {a.port != null && a.mode ? ` · ${a.mode}` : ''}
                </text>
              </g>
            )
          })}

          {/* The pill sits ON the line, half in each zone: that is what
              dual-homed means, and it is the only thing on the line. */}
          <rect x={X0 + 6} y={b.y - 14} width={126} height={28} rx={14}
                fill="#fff" stroke="#475569" strokeWidth="1.5" />
          <text x={X0 + 69} y={b.y + 4} textAnchor="middle" className="zd-svc">
            {b.service}
          </text>
          <text x={X0 + 69} y={b.y + 26} textAnchor="middle"
                className="zd-flow zd-dim">
            {b.direction}
          </text>

          {(() => {
            const blocks = (b.denied || []).map(d => wrap(d, 40))
            const total = blocks.reduce((n, l) => n + l.length, 0)
                        + blocks.length - 1
            let row = 0
            return blocks.map((lines, j) => {
              const g = (
                <text key={`d${j}`} x={X0 + BAND_W + 22}
                      y={b.y - (total * 11) / 2 + row * 11} className="zd-deny">
                  {lines.map((ln, k) => (
                    <tspan key={k} x={X0 + BAND_W + 22} dy={k ? 11 : 0}>
                      {k === 0 ? `✕ ${ln}` : `   ${ln}`}
                    </tspan>
                  ))}
                </text>
              )
              row += lines.length + 1
              return g
            })
          })()}
        </g>
      ))}

      {bands.map(({ z, y }) => {
        const c = ZONE_COLORS[z.name] || '#475569'
        return (
          <g key={z.id}>
            <rect x={X0} y={y} width={BAND_W} height={BAND_H} rx={8}
                  fill={c} fillOpacity="0.06" stroke={c} strokeWidth="2" />
            <rect x={X0 + 12} y={y + 12} width={62} height={17} rx={4} fill={c} />
            <text x={X0 + 43} y={y + 24} textAnchor="middle" className="zd-badge">
              ZONE {z.id}
            </text>
            <text x={X0 + 84} y={y + 25} className="zd-name">{z.name}</text>
            <text x={X0 + BAND_W - 12} y={y + 25} textAnchor="end"
                  className={z.egress ? 'zd-tag zd-tag-warn' : 'zd-tag zd-tag-ok'}>
              {z.egress ? 'egress' : 'no egress'}
            </text>
            <text x={X0 + 13} y={y + 46} className="zd-flow zd-dim">
              {z.subnet} · {(z.protocols || []).join(', ')}
              {z.latency_budget_ms != null
                ? ` · ${z.latency_budget_ms}ms budget` : ''}
            </text>
            {(() => {
              // Members share one row, so the pill shrinks to fit the widest
              // zone rather than spilling past the band it belongs to.
              const n = (z.members || []).length
              if (!n) return null
              const pitch = Math.min(116, (BAND_W - 26) / n)
              const w = pitch - 6
              return z.members.map((m, i) => (
                <g key={m}>
                  <rect x={X0 + 13 + i * pitch} y={y + 58} width={w} height={20}
                        rx={4} fill="#fff" stroke={c} strokeOpacity="0.45" />
                  <text x={X0 + 13 + i * pitch + w / 2} y={y + 72}
                        textAnchor="middle" className="zd-member">{m}</text>
                </g>
              ))
            })()}
          </g>
        )
      })}
    </svg>
  )
}

// The zone cards that used to live here said the same thing as the diagram
// above, one zone at a time. What the diagram cannot show is the CI contract,
// so that is all this renders now.
function ZoneInvariants({ zones }) {
  if (!zones) return <div className="muted">loading topology…</div>
  return (
    <div className="invariants">
      <strong>Enforced invariants</strong>
      <ul>
        {zones.invariants.map(i => (
          <li key={i.id}><code>{i.id}</code> {i.rule}</li>
        ))}
      </ul>
    </div>
  )
}

function ScenarioPanel({ tools }) {
  const [downed, setDowned] = useState([])
  const [result, setResult] = useState(null)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState(null)

  const toggle = (id) =>
    setDowned(d => d.includes(id) ? d.filter(x => x !== id) : [...d, id])

  const run = async () => {
    setBusy(true); setErr(null); setResult(null)
    try {
      const r = await fetch('/api/scenario/compare', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          tool_overrides: downed.map(id => ({ tool_id: id, online: false })),
        }),
      })
      const j = await r.json()
      if (!r.ok) setErr(j.error || 'scenario failed')
      else setResult(j)
    } catch (e) { setErr(String(e)) }
    setBusy(false)
  }

  return (
    <div>
      <p className="muted">
        Take tools down and re-plan. Runs the same C++ planner the dispatcher
        uses, against a cloned registry — never the live fab.
      </p>
      <div className="tool-grid">
        {tools.map(t => (
          <button
            key={t}
            onClick={() => toggle(t)}
            className={downed.includes(t) ? 'tool tool-down' : 'tool'}>
            {t}{downed.includes(t) && <span className="down-x"> DOWN</span>}
          </button>
        ))}
      </div>
      <button className="run" onClick={run} disabled={busy}>
        {busy ? 'solving…' : `Re-plan with ${downed.length} tool(s) down`}
      </button>

      {err && <div className="err">{err}</div>}

      {result && (
        <div className="result">
          <div className="stats-row">
            <Stat label="baseline assigned" value={result.baseline.assigned} />
            <Stat label="scenario assigned" value={result.scenario.assigned}
                  accent={result.diff.assigned_delta < 0 ? '#b91c1c' : '#15803d'} />
            <Stat label="delta" value={result.diff.assigned_delta > 0
                    ? `+${result.diff.assigned_delta}` : result.diff.assigned_delta} />
            <Stat label="solve" value={`${result.scenario.solve_ms.toFixed(2)} ms`}
                  sub={result.scenario.solver_linked
                    ? result.scenario.solver : `${result.scenario.solver} (greedy fallback)`} />
          </div>

          {result.diff.rerouted.length > 0 && (
            <>
              <h4>Rerouted</h4>
              <table className="tbl">
                <thead><tr><th>lot</th><th>from</th><th>to</th></tr></thead>
                <tbody>
                  {result.diff.rerouted.map(r => (
                    <tr key={r.lot_id}>
                      <td><code>{r.lot_id}</code></td>
                      <td className="from">{r.from}</td>
                      <td className="to">{r.to}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </>
          )}

          {result.diff.newly_unassigned.length > 0 && (
            <>
              <h4 className="danger">Newly unassignable</h4>
              <div className="chips">
                {result.diff.newly_unassigned.map(l => (
                  <span key={l} className="chip chip-bad">{l}</span>
                ))}
              </div>
            </>
          )}

          {result.scenario.unassigned.length > 0 && (
            <>
              <h4>Held, with reason</h4>
              <table className="tbl">
                <thead><tr><th>lot</th><th>recipe</th><th>reason</th></tr></thead>
                <tbody>
                  {result.scenario.unassigned.slice(0, 12).map(u => (
                    <tr key={u.lot_id}>
                      <td><code>{u.lot_id}</code></td>
                      <td>{u.recipe}</td>
                      <td className="muted">{u.reason}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </>
          )}
        </div>
      )}
    </div>
  )
}

function ToolDetail({ id, backHref }) {
  const [t, setT] = useState(null)
  const [err, setErr] = useState(null)

  useEffect(() => {
    let live = true
    const load = () => fetch(`/api/tools/${encodeURIComponent(id)}`)
      .then(r => r.ok ? r.json() : r.json().then(j => Promise.reject(j.error)))
      .then(j => { if (live) { setT(j); setErr(null) } })
      .catch(e => live && setErr(String(e)))
    load()
    // Poll rather than filter the SSE feed: the server owns the rollup, and a
    // second client-side derivation of the same numbers would drift from it.
    const iv = setInterval(load, 2000)
    return () => { live = false; clearInterval(iv) }
  }, [id])

  if (err) return <div><a className="link" href={backHref}>← tools</a><div className="err">{err}</div></div>
  if (!t) return <div className="muted">loading {id}…</div>

  return (
    <div>
      <a className="link" href={backHref}>← all tools</a>
      <div className="tool-head">
        <h3>{t.id}</h3>
        <span className={t.online ? 'tag tag-ok' : 'tag tag-warn'}>
          {t.online ? 'online' : 'down'}
        </span>
        <span className="muted">{t.group}</span>
      </div>

      <div className="stats-row">
        <Stat label="queue" value={t.queue ?? '—'} sub="at last decision" />
        <Stat label="running" value={t.running_count} sub="lots in flight" />
        <Stat label="dispatches" value={t.dispatches} />
        <Stat label="lots out" value={t.lots} />
        <Stat label="completed" value={t.completed} />
        <Stat label="changeovers" value={t.changeovers} sub={t.setup || 'no setup'} />
      </div>

      {t.running_count > 0 && (
        <>
          <h4>In flight</h4>
          <div className="chips">
            {t.running.map(l => <span key={l} className="chip">{l}</span>)}
          </div>
        </>
      )}

      <h4>Recent decisions</h4>
      {t.recent_decisions.length === 0 ? (
        <div className="muted">no decisions recorded for this tool yet</div>
      ) : (
        <table className="tbl">
          <thead><tr><th>sim day</th><th>queue</th><th>lots</th><th>setup</th></tr></thead>
          <tbody>
            {t.recent_decisions.map((d, i) => (
              <tr key={i}>
                <td><code>{d.day ?? '—'}</code></td>
                <td>{d.queue ?? '—'}</td>
                <td>{d.lots ?? '—'}</td>
                <td className="muted">{d.setup || '—'}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  )
}

// Filters live in the URL rather than in component state: "the ETCH tools
// that are down" is the thing people actually want to send to someone, and a
// tab that forgets its filter on every visit is a tab you re-type into.
function ToolIndex({ query, setQuery, toolHref }) {
  const [data, setData] = useState(null)
  const [open, setOpen] = useState({})
  const q = query.q || ''
  const setQ = v => setQuery({ q: v || undefined })
  const type = query.type || 'all'
  const setType = v => setQuery({ type: v === 'all' ? undefined : v })
  // Delay_* are queue-time placeholders pinned near 100% busy. Left in, they
  // top the ranking by dispatch count and bury the real constraint.
  const showDelay = query.delay === '1'
  const setShowDelay = v => setQuery({ delay: v ? '1' : undefined })

  useEffect(() => {
    let live = true
    const load = () => fetch('/api/tools').then(r => r.json())
      .then(j => live && setData(j)).catch(() => {})
    load()
    const iv = setInterval(load, 4000)
    return () => { live = false; clearInterval(iv) }
  }, [])

  if (!data) return <div className="muted">loading tools…</div>

  const needle = q.trim().toLowerCase()
  const isDelay = g => g.toLowerCase().startsWith('delay')

  const groups = data.groups
    .filter(g => showDelay || !isDelay(g.group))
    .filter(g => type === 'all' || g.group === type)
    .map(g => ({ ...g, tools: needle ? g.tools.filter(t => t.id.toLowerCase().includes(needle)) : g.tools }))
    .filter(g => g.tools.length > 0)

  const delayCount = data.groups.filter(g => isDelay(g.group)).length

  return (
    <div>
      <ToolAvailability />
      <div className="tool-index-head">
        <p className="muted">
          {data.total} tools in {data.groups.length} groups, busiest first.
          A high queue with the tool online is where lots are waiting.
        </p>
        <div className="tool-filters">
          <select className="tool-search" value={type} onChange={e => setType(e.target.value)}>
            <option value="all">all types ({data.groups.length})</option>
            {data.groups.map(g => (
              <option key={g.group} value={g.group}>{g.group} ({g.count})</option>
            ))}
          </select>
          <input className="tool-search" placeholder="filter tools…"
                 value={q} onChange={e => setQ(e.target.value)} />
          {delayCount > 0 && (
            <label className="heat-toggle">
              <input type="checkbox" checked={showDelay}
                     onChange={e => setShowDelay(e.target.checked)} />
              {' '}show Delay_*
            </label>
          )}
        </div>
      </div>

      {groups.map(g => {
        // Searching implies you want to see matches, so a filtered group opens
        // itself rather than making you expand every one.
        const isOpen = needle ? true : !!open[g.group]
        return (
          <div key={g.group} className="tgroup">
            <button className="tgroup-head" onClick={() => setOpen(o => ({ ...o, [g.group]: !o[g.group] }))}>
              <span className="tgroup-caret">{isOpen ? '▾' : '▸'}</span>
              <strong>{g.group}</strong>
              <span className="muted">{g.count} tools</span>
              <span className="tgroup-metrics">
                <span>{g.dispatches.toLocaleString()} dispatches</span>
                {g.queue_max != null && <span>queue max {g.queue_max}</span>}
                {g.offline > 0 && <span className="danger">{g.offline} down</span>}
              </span>
            </button>
            {isOpen && (
              <div className="tgroup-body">
                {g.tools.map(t => (
                  <a key={t.id} className={t.online ? 'tcard' : 'tcard tcard-down'}
                     href={toolHref(t.id)}>
                    <div className="tcard-id">{t.id}</div>
                    <div className="tcard-row">
                      <span>q {t.queue ?? '—'}</span>
                      <span>{t.dispatches} disp</span>
                      {!t.online && <span className="danger">down</span>}
                    </div>
                  </a>
                ))}
              </div>
            )}
          </div>
        )
      })}
    </div>
  )
}

// The bench results page is a self-contained static page with its own design
// system, framed rather than ported: it fetches nothing, its data is embedded,
// and rewriting 145KB of hand-tuned SVG into React would lose fidelity and buy
// nothing. Routes used to be framed the same way and no longer are -- see
// RoutePages.jsx: routes needed per-product URLs, which an iframe cannot have.
function Embedded({ src, title }) {
  return (
    <div className="embed-wrap">
      <iframe src={src} title={title} className="embed-frame" loading="lazy" />
      <div className="embed-foot muted">
        Static page ·{' '}
        <a href={src} target="_blank" rel="noreferrer">open full screen</a>
      </div>
    </div>
  )
}


// ---------------------------------------------------------------------------
// Playback badge. The feed (bench/tools/sim_feed.py) replays a simulated fab
// at a multiple of realtime, so "live" alone was ambiguous: 400x live and 1x
// live look identical in the header but mean very different things about what
// a minute of watching is worth. The badge states the rate and, on click,
// lets you change it or hold the run still.
// ---------------------------------------------------------------------------

const SPEED_HELP =
  'Playback speed — how fast the simulated fab is replayed. 1x is realtime ' +
  '(one simulated second per second); 400x replays a simulated day in about ' +
  'three and a half minutes. Speed changes pacing only: the run itself is ' +
  'unchanged, so the same seed gives the same fab at every speed.'

function fmtSpeed(v) {
  if (v === null || v === undefined) return '—'
  return `${Number.isInteger(v) ? v : Number(v).toFixed(1)}x`
}

// WIP against the fab's own clock.
//
// The series is fab data, so wall clock is the wrong axis for it: the feed
// replays at 1x to 400x and can be paused, which means equal spacing between
// arrivals is not equal fab time. Plotted by arrival, the same-looking window
// covered ten minutes of fab at 1x and nearly three days at 400x, a slope meant
// 400x different things depending on the pill in the header, and a paused feed
// kept scrolling out a flat line that read as steady WIP rather than a stopped
// fab. Against sim time all three are gone by construction.
//
// The event-rate chart on the topology tab deliberately keeps its wall-clock
// axis: that one measures envelopes arriving at this browser, so the browser's
// clock is its subject rather than a distortion of it.
// Why the chart is standing still, when it is. A still chart is the honest
// rendering of a fab clock that is not advancing, but on its own it is
// indistinguishable from a dead stream, so it says which it is. Paused is the
// deliberate case; a clock that has simply stopped moving while frames keep
// arriving is a finished replay or a stalled feed, and is worth saying out
// loud rather than leaving as a flat line nobody questions.
function stalledNote(last) {
  if (!last) return null
  if (last.paused) return 'paused · the fab clock is stopped'
  if (last.simAt == null) return null
  const age = Date.now() / 1000 - last.simAt
  if (age < 30) return null
  return `fab clock last advanced ${age < 90 ? `${Math.round(age)}s`
    : `${Math.round(age / 60)} min`} ago`
}

function WipChart({ history }) {
  const last = history.length ? history[history.length - 1] : null
  // Null when nothing on the wire is sim-stamped (an external Kafka feed, or
  // --no-burndown with no dispatch decisions yet). Then this falls back to the
  // old wall-clock axis and says so, rather than inventing a clock.
  const timed = history.length > 1 && last?.simT != null

  // Only consulted when the entire window is paused, which is the one case
  // spanFor cannot measure its way out of.
  const lastSpan = useRef(0)
  const span = useMemo(
    () => (timed ? spanFor(history.map(h => h.simT), 120, lastSpan.current) : 0),
    [history, timed],
  )
  useEffect(() => { if (span > 0) lastSpan.current = span }, [span])

  // Where playback speed changed, in sim time. Each one is a point either side
  // of which a pixel is worth a different amount of fab time.
  const marks = useMemo(() => {
    if (!timed) return []
    const out = []
    for (let i = 1; i < history.length; i++) {
      const a = history[i - 1].speed, b = history[i].speed
      if (a != null && b != null && a !== b && history[i].simT != null) {
        out.push({ t: history[i].simT, label: fmtSpeed(b) })
      }
    }
    return out
  }, [history, timed])

  return (
    <>
      <StreamChart
        data={history} cap={120} height={240} yLabel="lots"
        xMode={timed ? 'time' : 'index'} span={span} marks={marks}
        frozen={timed ? stalledNote(last) : null}
        series={[
          { key: 'ready', name: 'waiting', color: '#1d4ed8' },
          { key: 'inFlight', name: 'running', color: '#15803d' },
        ]} />
      <p className="muted" style={{ fontSize: 11 }}>
        {timed ? (
          <>
            x axis is simulated fab time, {fmtSpan(span)} across the plot
            {last.speed != null && <> at {fmtSpeed(last.speed)} playback</>}
            {' '}· now {fmtSimTime(last.simT)}. Changing speed rescales the
            window and marks where it changed; pausing stops the chart, because
            the fab clock has stopped.
          </>
        ) : (
          <>x axis is wall-clock arrival time: this feed publishes no simulated
             clock, so fab time is not available to plot against</>
        )}
      </p>
    </>
  )
}

// ---------------------------------------------------------------------------
// Timeline provenance. What simulated day is on screen, and which producer run
// it came from.
//
// This exists because the dashboard once drew a day-5 WIP snapshot against a
// day-30 decision stream and looked entirely healthy doing it. Two producers,
// two timelines, one mirror, and nothing on screen that could have told you.
// The original dashboard spec asked for a replay id in the provenance line for
// exactly this reason; this is that.
// ---------------------------------------------------------------------------

function TimelineBadge({ state }) {
  const tl = state && state.timeline
  if (!tl) return null

  // The day worth showing is the live one; the snapshot day is where the run
  // started and only matters when the two disagree.
  const day = tl.stream_day ?? tl.snapshot_day
  const run = tl.stream_run || tl.snapshot_run

  // consistent === null means one side has not been seen yet. That is not a
  // fault, and flagging it as one would teach people to ignore the badge.
  const broken = tl.consistent === false

  const title = broken
    ? `Timeline mismatch: snapshot is run ${tl.snapshot_run} at day `
      + `${tl.snapshot_day ?? '?'}, stream is run ${tl.stream_run} at day `
      + `${tl.stream_day ?? '?'}. The view is stitching two runs together and `
      + `should not be trusted. Restart the feed as a single producer with `
      + `--warmup-days N.`
    : `Simulated day ${day ?? '—'}, producer run ${run || '—'}. `
      + `Snapshot and live stream agree.`

  return (
    <div className={broken ? 'tl-badge tl-badge-bad' : 'tl-badge'} title={title}>
      {broken && <span className="tl-warn" aria-hidden="true">⚠</span>}
      <span className="tl-day">
        day {day == null ? '—' : Number(day).toFixed(1)}
      </span>
      {run && <span className="tl-run">{run}</span>}
      {broken && <span className="tl-bad-text">timeline mismatch</span>}
    </div>
  )
}

function SpeedControl({ connected }) {
  const [ctl, setCtl] = useState(null)
  const [open, setOpen] = useState(false)
  const [err, setErr] = useState(null)
  const box = useRef(null)

  // Polled, not streamed: the feed is a separate process that can be
  // restarted or re-flagged out of band, so the header has to re-read the
  // truth rather than trust the last thing this tab posted.
  useEffect(() => {
    let alive = true
    const load = () => fetch('/api/sim/control')
      .then(r => r.json())
      .then(j => { if (alive) setCtl(j) })
      .catch(() => {})
    load()
    const iv = setInterval(load, 5000)
    return () => { alive = false; clearInterval(iv) }
  }, [])

  // Close on outside click and on Escape, so the popover never strands the
  // rest of the header behind it.
  useEffect(() => {
    if (!open) return undefined
    const onDown = e => { if (box.current && !box.current.contains(e.target)) setOpen(false) }
    const onKey = e => { if (e.key === 'Escape') setOpen(false) }
    document.addEventListener('mousedown', onDown)
    document.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('mousedown', onDown)
      document.removeEventListener('keydown', onKey)
    }
  }, [open])

  const send = body => {
    // Optimistic: at 400x the round trip is longer than the next repaint, and
    // a button that lags half a second reads as broken. The 5s poll corrects
    // it if the POST fails.
    setCtl(c => ({ ...c, ...body }))
    setErr(null)
    fetch('/api/sim/control', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    })
      .then(async r => {
        const j = await r.json().catch(() => ({}))
        if (!r.ok) throw new Error(j.error || `HTTP ${r.status}`)
        setCtl(j)
      })
      .catch(e => setErr(String(e.message || e)))
  }

  const available = !!ctl?.available
  const paused = !!ctl?.paused
  const speeds = ctl?.speeds?.length ? ctl.speeds : [1, 10, 20, 50, 100, 400]

  const label = !connected ? 'reconnecting'
    : paused ? 'paused'
      : available ? `live · ${fmtSpeed(ctl.speed)}` : 'live'

  const title = available
    ? `${SPEED_HELP}\n\nClick to pause or change speed.`
    : 'Live stream from the API. No simulator feed is reporting a playback ' +
      'speed, so there is nothing to pace here.'

  const cls = !connected ? 'live live-off' : paused ? 'live live-paused' : 'live live-on'

  return (
    <div className="live-wrap" ref={box}>
      <button
        type="button"
        className={cls}
        title={title}
        aria-haspopup={available ? 'dialog' : undefined}
        aria-expanded={open}
        disabled={!available}
        onClick={() => setOpen(o => !o)}
      >
        <span className="dot" />{label}
        {available && <span className="live-caret">▾</span>}
      </button>

      {open && available && (
        <div className="speed-pop" role="dialog" aria-label="Playback speed">
          <div className="speed-pop-head">
            <strong>Playback</strong>
            <span className="muted">sim time per wall second</span>
          </div>
          <button
            type="button"
            className="speed-toggle"
            onClick={() => send({ paused: !paused })}
          >
            {paused ? '▶  Resume' : '❚❚  Pause'}
          </button>
          <div className="speed-grid">
            {speeds.map(v => (
              <button
                key={v}
                type="button"
                className={Number(ctl.speed) === v ? 'active' : ''}
                onClick={() => send({ speed: v, paused: false })}
              >{v}x</button>
            ))}
          </div>
          <p className="speed-note">{SPEED_HELP}</p>
          {err && <p className="speed-err">{err}</p>}
        </div>
      )}
    </div>
  )
}

export default function App() {
  const { state, feed, connected, history, link } = useLiveState()
  const [zones, setZones] = useState(null)
  // The URL is the single source of truth for "where am I": tab, open tool and
  // every filter. Reload, back button, and a link pasted into chat all land on
  // the same view.
  const { segments, query, navigate, setQuery } = useRoute()
  const tab = TABS.includes(segments[0]) ? segments[0] : 'live'
  const openTool = tab === 'tools' ? (segments[1] || null) : null
  const openProduct = tab === 'routes' ? (segments[1] || null) : null
  // Remembered per browser so the rail does not reappear every reload for
  // someone who closed it. Wrapped: some contexts throw on storage access.
  const [assistantOpen, setAssistantOpen] = useState(() => {
    try { return localStorage.getItem('assistantOpen') === '1' } catch { return false }
  })
  useEffect(() => {
    try { localStorage.setItem('assistantOpen', assistantOpen ? '1' : '0') } catch { /* ignore */ }
  }, [assistantOpen])

  useEffect(() => {
    fetch('/api/zones').then(r => r.json()).then(setZones).catch(() => {})
  }, [])

  const tools = useMemo(() => {
    const fromState = state ? Object.keys(state.tools) : []
    return fromState.length ? fromState : [
      'ETCH_11','ETCH_12','ETCH_13','FURN_02','FURN_03','CVD_07','CVD_08',
      'LITHO_03','LITHO_04','CD_SEM_01','PROBE_21','PROBE_22',
    ]
  }, [state])

  const offline = state
    ? Object.entries(state.tools).filter(([, v]) => !v.online).map(([k]) => k) : []

  return (
    <div className={assistantOpen ? 'app app-railed' : 'app'}>
      <header>
        <div>
          <h1>Fab Dispatch</h1>
          <div className="sub">
            zone 3 · enterprise · read-only mirror ·{' '}
            {/* Served by the API, not the SPA, so it is a real navigation
                rather than a router link -- target=_blank keeps the dashboard
                and its live stream up while you read. */}
            <a className="sub-link" href="/docs" target="_blank" rel="noreferrer"
               title="Swagger UI, generated from the API's URL map">API docs</a>
          </div>
        </div>
        {/* The live pill and, when the rail is closed, the only way back into
            the assistant -- kept together in the top-right corner. */}
        <div className="header-right">
          <TimelineBadge state={state} />
          <SpeedControl connected={connected} />
          {!assistantOpen && (
            <button className="rail-reopen" onClick={() => setAssistantOpen(true)}
                    title="Open the assistant">
              <span className="rail-reopen-icon" aria-hidden="true">&#9776;</span>
              Assistant
            </button>
          )}
        </div>
      </header>

      <div className="stats-row">
        <Stat label="ready" value={state?.ready ?? '—'} />
        <Stat label="in flight" value={state?.in_flight ?? '—'} />
        <Stat label="completed" value={state?.completed ?? '—'} />
        <Stat label="throughput" value={state ? state.throughput_lots_per_hour : '—'}
              sub="lots/hr" />
        <Stat label="tools down" value={offline.length}
              accent={offline.length ? '#b91c1c' : undefined}
              sub={offline.join(', ') || 'all up'}
              href={linkTo('/tools')}
              title="open the tool index" />
      </div>

      <div className="shell">
        <div className="shell-main">

      <nav className="tabs">
        {TABS.map(t => (
          <a key={t} className={tab === t ? 'active' : ''} href={linkTo(`/${t}`)}>
            {t}
          </a>
        ))}
      </nav>

      {tab === 'lots' && (
        <div className="grid-wide">
          <section>
            <h3>Cohort burndown</h3>
            <p className="muted" style={{ marginTop: -4 }}>
              Remaining route steps per lot against simulated time. A cohort is
              one product's releases within one day &mdash; the lots that can
              actually batch together, since an SMT2020 furnace batch needs the
              same product <i>and</i> the same step. Band thickness is cohort
              spread; a widening band means the cohort is desynchronising.
            </p>
            <CohortBurndown cohort={query.cohort || null}
                            onCohort={c => setQuery({ cohort: c || undefined })}
                            routeHref={p => linkTo(['routes', p])} />
          </section>
        </div>
      )}

      {tab === 'live' && (
        <div className="grid">
          <section>
            <h3>WIP — whole fab</h3>
            <p className="muted" style={{ marginTop: -4 }}>
              Every lot in the fab, not one tool. <b>Waiting</b> has been
              released but not started; <b>running</b> is on a tool now. Their
              sum is total WIP. For one tool, use the tools or floor tab.
            </p>
            <WipChart history={history} />
          </section>
          <section>
            <h3>Event feed</h3>
            <div className="feed">
              {feed.length === 0 && <div className="muted">waiting for events…</div>}
              {feed.map((m, i) => (
                <div key={i} className="feed-row">
                  <span className={`badge b-${(m.event?.type || 'DECISION').toLowerCase()}`}>
                    {m.event?.type || 'DECISION'}
                  </span>
                  <code>{m.event?.lot || m.decision?.lot || ''}</code>
                  <span className="muted">{m.event?.tool || m.decision?.tool || ''}</span>
                </div>
              ))}
            </div>
          </section>
        </div>
      )}

      {tab === 'tools' && (
        <section>
          {openTool
            ? <ToolDetail id={openTool} backHref={linkTo('/tools')} />
            : <><h3>Tools</h3>
                <ToolIndex query={query} setQuery={setQuery}
                           toolHref={id => linkTo(['tools', id])} /></>}
        </section>
      )}

      {tab === 'floor' && (
        <section>
          <h3>Cleanroom floor</h3>
          <FloorMap onOpenTool={(id) => navigate(['tools', id])}
                    sel={query.bay || null}
                    onSel={(k) => setQuery({ bay: k || undefined })}
                    heat={query.heat === '1'}
                    onHeat={(v) => setQuery({ heat: v ? '1' : undefined })} />
        </section>
      )}

      {tab === 'routes' && (
        <section>
          {openProduct
            ? <RouteProduct product={openProduct}
                            backHref={linkTo('/routes')}
                            cohortHref={c => linkTo('/lots', { cohort: c })} />
            : <><h3>Product routes</h3>
                <RouteIndex hrefFor={id => linkTo(['routes', id])} /></>}
        </section>
      )}

      {tab === 'results' && (
        <section>
          <h3>Simulation results</h3>
          <Embedded src="/bench-results.html" title="Fab Dispatch Bench" />
        </section>
      )}

      {tab === 'scenario' && (
        <section><h3>What-if</h3><ScenarioPanel tools={tools} /></section>
      )}

      {tab === 'topology' && (
        <>
          <TopologyMetrics zones={zones} state={state} link={link}
                           connected={connected} />
          <section>
            <h3>Network segmentation</h3>
            <ZoneDiagram zones={zones} />
            <ZoneInvariants zones={zones} />
          </section>
        </>
      )}

      <footer className="muted">
        No write path exists from this page to the dispatcher. Scenario runs use
        a cloned registry in the same C++ planner binary.
      </footer>

        </div>

        {/* Always mounted, never unmounted by tab changes: the conversation
            has to survive switching between live, scenario and topology, which
            is the whole point of it being a rail rather than a tab. Collapsing
            hides it with [hidden] so chat state is preserved. */}
        <aside className="rail" hidden={!assistantOpen}>
          <div className="rail-head">
            <h3>Assistant</h3>
            <button className="rail-toggle" onClick={() => setAssistantOpen(false)}
                    title="Hide assistant">×</button>
          </div>
          <ChatPanel />
        </aside>
      </div>
    </div>
  )
}
