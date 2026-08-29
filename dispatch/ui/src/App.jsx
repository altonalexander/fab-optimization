import { useEffect, useMemo, useRef, useState } from 'react'
import ChatPanel from './ChatPanel.jsx'
import {
  LineChart, Line, XAxis, YAxis, Tooltip, ResponsiveContainer, CartesianGrid,
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
  const feedRef = useRef([])

  useEffect(() => {
    let es
    const connect = () => {
      es = new EventSource('/api/stream')
      es.onopen = () => setConnected(true)
      es.onerror = () => { setConnected(false); es.close(); setTimeout(connect, 3000) }
      es.onmessage = (e) => {
        const msg = JSON.parse(e.data)
        if (msg.kind === 'state') {
          setState(msg.state)
          setHistory(h => [...h.slice(-119), {
            t: new Date().toLocaleTimeString(),
            ready: msg.state.ready,
            inFlight: msg.state.in_flight,
            completed: msg.state.completed,
          }])
        } else if (msg.kind === 'event' || msg.kind === 'decision') {
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

  return { state, feed, connected, history }
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

function ZoneMap({ zones }) {
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

export default function App() {
  const { state, feed, connected, history } = useLiveState()
  const [zones, setZones] = useState(null)
  const [tab, setTab] = useState('live')

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

      <nav className="tabs">
        {['live', 'assistant', 'scenario', 'topology'].map(t => (
          <button key={t} className={tab === t ? 'active' : ''} onClick={() => setTab(t)}>
            {t}
          </button>
        ))}
      </nav>

      {tab === 'live' && (
        <div className="grid">
          <section>
            <h3>WIP</h3>
            <ResponsiveContainer width="100%" height={240}>
              <LineChart data={history}>
                <CartesianGrid strokeDasharray="3 3" stroke="#e5e7eb" />
                <XAxis dataKey="t" tick={{ fontSize: 10 }} minTickGap={40} />
                <YAxis tick={{ fontSize: 11 }} />
                <Tooltip />
                <Line type="monotone" dataKey="ready" stroke="#1d4ed8" dot={false} />
                <Line type="monotone" dataKey="inFlight" stroke="#15803d" dot={false} />
              </LineChart>
            </ResponsiveContainer>
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

      {tab === 'assistant' && (
        <section><h3>Assistant</h3><ChatPanel /></section>
      )}

      {tab === 'scenario' && (
        <section><h3>What-if</h3><ScenarioPanel tools={tools} /></section>
      )}

      {tab === 'topology' && (
        <section><h3>Network segmentation</h3><ZoneMap zones={zones} /></section>
      )}

      <footer className="muted">
        No write path exists from this page to the dispatcher. Scenario runs use
        a cloned registry in the same C++ planner binary.
      </footer>
    </div>
  )
}
