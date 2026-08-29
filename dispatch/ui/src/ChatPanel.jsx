import { useEffect, useRef, useState } from 'react'

// Grounded assistant. Every figure in a reply comes from a tool result run
// against live state or the C++ scenario planner — never from model recall.
// Read-only: it can inspect and simulate, not change the fab.

const SUGGESTIONS = [
  'What is the fab doing right now?',
  'What happens if LITHO_03 goes down?',
  'Why are lots sitting unassigned?',
  'Which tool is the bottleneck?',
]

const TOOL_LABEL = {
  get_fab_state: 'read live state',
  get_recent_events: 'read event feed',
  run_scenario: 'ran what-if',
  explain_unassigned: 'checked held lots',
}

export default function ChatPanel() {
  const [status, setStatus] = useState(null)
  const [msgs, setMsgs] = useState([])
  const [input, setInput] = useState('')
  const [busy, setBusy] = useState(false)
  const endRef = useRef(null)

  useEffect(() => {
    fetch('/api/chat/status').then(r => r.json()).then(setStatus).catch(() => {})
  }, [])

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [msgs, busy])

  const send = async (text) => {
    const q = (text ?? input).trim()
    if (!q || busy) return
    const next = [...msgs, { role: 'user', content: q }]
    setMsgs(next); setInput(''); setBusy(true)

    try {
      const r = await fetch('/api/chat', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        // Send only role/content; tool metadata stays client-side.
        body: JSON.stringify({
          messages: next.map(m => ({ role: m.role, content: m.content })),
        }),
      })
      const j = await r.json()
      setMsgs(m => [...m, {
        role: 'assistant',
        content: j.reply || `Unavailable: ${j.error}`,
        tools: j.tools_used || [],
        failed: !j.reply,
      }])
    } catch (e) {
      setMsgs(m => [...m, { role: 'assistant', content: String(e), failed: true }])
    }
    setBusy(false)
  }

  if (status && !status.available) {
    return (
      <div className="chat-off">
        <strong>Assistant unavailable</strong>
        <div className="muted">{status.error}</div>
        <div className="muted" style={{ marginTop: 8 }}>
          Needs <code>anthropic[vertex]</code> and <code>GOOGLE_CLOUD_PROJECT</code>.
          Claude is served from Vertex AI in <code>{status.model}</code>.
        </div>
      </div>
    )
  }

  return (
    <div className="chat">
      <div className="chat-log">
        {msgs.length === 0 && (
          <div className="chat-empty">
            <p className="muted">
              Ask about live state or run a what-if. Answers are grounded in
              tool results — live Kafka state and the same C++ planner the
              dispatcher uses.
            </p>
            <div className="chips">
              {SUGGESTIONS.map(s => (
                <button key={s} className="chip chip-btn" onClick={() => send(s)}>
                  {s}
                </button>
              ))}
            </div>
          </div>
        )}

        {msgs.map((m, i) => (
          <div key={i} className={`msg msg-${m.role}`}>
            {m.tools?.length > 0 && (
              <div className="tool-trace">
                {m.tools.map((t, j) => (
                  <span key={j} className="trace">
                    {TOOL_LABEL[t.tool] || t.tool}
                    {t.input?.tools_down?.length
                      ? `: ${t.input.tools_down.join(', ')}` : ''}
                  </span>
                ))}
              </div>
            )}
            <div className={m.failed ? 'bubble bubble-err' : 'bubble'}>
              {m.content}
            </div>
          </div>
        ))}

        {busy && (
          <div className="msg msg-assistant">
            <div className="bubble muted">thinking…</div>
          </div>
        )}
        <div ref={endRef} />
      </div>

      <div className="chat-input">
        <input
          value={input}
          placeholder="Ask about the fab…"
          onChange={e => setInput(e.target.value)}
          onKeyDown={e => e.key === 'Enter' && send()}
          disabled={busy}
        />
        <button onClick={() => send()} disabled={busy || !input.trim()}>Send</button>
      </div>
      <div className="muted chat-foot">
        Read-only. The assistant can inspect and simulate; it cannot change the fab.
      </div>
    </div>
  )
}
