import { useEffect, useMemo, useRef, useState } from 'react'
import ChatPanel from './ChatPanel.jsx'
import FloorMap from './FloorMap.jsx'
import CohortBurndown from './CohortBurndown.jsx'
import {
  LineChart, Line, XAxis, YAxis, Tooltip, ResponsiveContainer, CartesianGrid, Legend,
} from 'recharts'

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
          setHistory(h => [...h.slice(-119), {
            t: new Date().toLocaleTimeString(),
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

function Stat({ label, value, sub, accent }) {
  return (
    <div className="stat">
      <div className="stat-label">{label}</div>
      <div className="stat-value" style={accent ? { color: accent } : undefined}>{value}</div>
      {sub && <div className="stat-sub">{sub}</div>}
    </div>
  )
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
            <ResponsiveContainer width="100%" height={160}>
              <LineChart data={link.rateHistory}>
                <CartesianGrid strokeDasharray="3 3" stroke="#e5e7eb" />
                <XAxis dataKey="t" tick={{ fontSize: 10 }} minTickGap={40} />
                <YAxis tick={{ fontSize: 11 }} />
                <Tooltip />
                <Line type="monotone" dataKey="rate" stroke="#1d4ed8" dot={false} />
              </LineChart>
            </ResponsiveContainer>
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

function ZoneMap({ zones, roles }) {
  if (!zones) return <div className="muted">loading topology…</div>
  return (
    <div className="zones">
      {zones.zones.map(z => (
        <div key={z.id} className="zone" style={{ borderColor: ZONE_COLORS[z.name] }}>
          <div className="zone-head">
            <span className="zone-id" style={{ background: ZONE_COLORS[z.name] }}>
              ZONE {z.id}
            </span>
            <strong>{z.name}</strong>
            {roles && roles[z.name] && (
              <span className="member">{roles[z.name]}</span>
            )}
            <span className={z.egress ? 'tag tag-warn' : 'tag tag-ok'}>
              {z.egress ? 'egress' : 'no egress'}
            </span>
          </div>
          <div className="zone-sub">{z.subnet} · {z.protocols.join(', ')}</div>
          <div className="members">
            {z.members.map(m => <span key={m} className="member">{m}</span>)}
          </div>
        </div>
      ))}
      <div className="invariants">
        <strong>Enforced invariants</strong>
        <ul>{zones.invariants.map(i => <li key={i.id}><code>{i.id}</code> {i.rule}</li>)}</ul>
      </div>
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

function ToolDetail({ id, onBack }) {
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

  if (err) return <div><button className="link" onClick={onBack}>← tools</button><div className="err">{err}</div></div>
  if (!t) return <div className="muted">loading {id}…</div>

  return (
    <div>
      <button className="link" onClick={onBack}>← all tools</button>
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

function ToolIndex({ onOpen }) {
  const [data, setData] = useState(null)
  const [open, setOpen] = useState({})
  const [q, setQ] = useState('')
  const [type, setType] = useState('all')
  // Delay_* are queue-time placeholders pinned near 100% busy. Left in, they
  // top the ranking by dispatch count and bury the real constraint.
  const [showDelay, setShowDelay] = useState(false)

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
                  <button key={t.id} className={t.online ? 'tcard' : 'tcard tcard-down'}
                          onClick={() => onOpen(t.id)}>
                    <div className="tcard-id">{t.id}</div>
                    <div className="tcard-row">
                      <span>q {t.queue ?? '—'}</span>
                      <span>{t.dispatches} disp</span>
                      {!t.online && <span className="danger">down</span>}
                    </div>
                  </button>
                ))}
              </div>
            )}
          </div>
        )
      })}
    </div>
  )
}

// The route explorer and the bench results are self-contained static pages
// with their own design system. They are framed rather than ported: rewriting
// 360KB of hand-tuned SVG into React would lose fidelity and buy nothing,
// since neither page fetches anything -- their data is embedded.
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

export default function App() {
  const { state, feed, connected, history, link } = useLiveState()
  const [zones, setZones] = useState(null)
  const [tab, setTab] = useState('live')
  const [openTool, setOpenTool] = useState(null)
  const topo = useMemo(() => analyzeTopology(zones), [zones])
  // Remembered per browser so the rail does not reappear every reload for
  // someone who closed it. Wrapped: some contexts throw on storage access.
  const [assistantOpen, setAssistantOpen] = useState(() => {
    try { return localStorage.getItem('assistantOpen') !== '0' } catch { return true }
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
    <div className="app">
      <header>
        <div>
          <h1>Fab Dispatch</h1>
          <div className="sub">zone 3 · enterprise · read-only mirror</div>
        </div>
        <div className={connected ? 'live live-on' : 'live live-off'}>
          <span className="dot" />{connected ? 'live' : 'reconnecting'}
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
              sub={offline.join(', ') || 'all up'} />
      </div>

      <div className={assistantOpen ? 'shell' : 'shell shell-collapsed'}>
        <div className="shell-main">

      <nav className="tabs">
        {['live', 'lots', 'tools', 'floor', 'routes', 'results', 'scenario', 'topology'].map(t => (
          <button key={t} className={tab === t ? 'active' : ''} onClick={() => setTab(t)}>
            {t}
          </button>
        ))}
      </nav>

      {tab === 'lots' && (
        <div className="grid">
          <section>
            <h3>Cohort burndown</h3>
            <p className="muted" style={{ marginTop: -4 }}>
              Remaining route steps per lot against simulated time. A cohort is
              one product's releases within one day &mdash; the lots that can
              actually batch together, since an SMT2020 furnace batch needs the
              same product <i>and</i> the same step. Band thickness is cohort
              spread; a widening band means the cohort is desynchronising.
            </p>
            <CohortBurndown />
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
            <ResponsiveContainer width="100%" height={240}>
              <LineChart data={history}>
                <CartesianGrid strokeDasharray="3 3" stroke="#e5e7eb" />
                <XAxis dataKey="t" tick={{ fontSize: 10 }} minTickGap={40} />
                <YAxis tick={{ fontSize: 11 }}
                       label={{ value: 'lots', angle: -90, position: 'insideLeft',
                                style: { fontSize: 11, fill: '#6b7280' } }} />
                <Tooltip />
                <Legend wrapperStyle={{ fontSize: 12 }} />
                <Line type="monotone" dataKey="ready" name="waiting"
                      stroke="#1d4ed8" dot={false} />
                <Line type="monotone" dataKey="inFlight" name="running"
                      stroke="#15803d" dot={false} />
              </LineChart>
            </ResponsiveContainer>
            {/* The x axis is wall clock, not simulated time: this is a live
                monitor, and the sim runs at --speed x realtime. */}
            <p className="muted" style={{ fontSize: 11 }}>
              x axis is wall-clock arrival time, not simulated time
            </p>
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
            ? <ToolDetail id={openTool} onBack={() => setOpenTool(null)} />
            : <><h3>Tools</h3><ToolIndex onOpen={setOpenTool} /></>}
        </section>
      )}

      {tab === 'floor' && (
        <section>
          <h3>Cleanroom floor</h3>
          <FloorMap onOpenTool={(id) => { setOpenTool(id); setTab('tools') }} />
        </section>
      )}

      {tab === 'routes' && (
        <section>
          <h3>Route explorer</h3>
          <Embedded src="/route-explorer.html" title="Fab Route Explorer" />
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
            <ZoneMap zones={zones} roles={topo && topo.roles} />
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

      {!assistantOpen && (
        <button className="rail-reopen" onClick={() => setAssistantOpen(true)}>
          Assistant
        </button>
      )}
    </div>
  )
}
