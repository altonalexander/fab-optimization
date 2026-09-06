import { useEffect, useMemo, useRef, useState } from 'react'

// ---------------------------------------------------------------------------
// Animated assistant character. Pure SVG + CSS transitions: no sprite sheets,
// no animation library. An idle loop schedules small, randomly spaced gestures
// (blink, glance, head tilt, nod) so the character never looks frozen, and a
// `mood` prop from the chat overrides the loop while something is happening:
//
//   idle       – ambient loop only
//   listening  – input focused: leans in, eyes to the reader, no wandering
//   thinking   – request in flight: eyes up and away, brows up, "…" bubble
//   speaking   – reply just landed: mouth cycles for a couple of seconds
//
// Desktop only: the parent mounts it behind a matchMedia check, and every
// transition is disabled under prefers-reduced-motion.
// ---------------------------------------------------------------------------

// Each character is a palette plus a "hat" decoration so they read as
// different people at a glance while sharing one rig (same eyes, mouth and
// head geometry, so every gesture works for all of them).
export const CHARACTERS = {
  ada: {
    name: 'Ada',
    role: 'shift lead',
    skin: '#f3c9a6', hair: '#3b2a1e', shirt: '#1d4ed8', accent: '#facc15',
    hat: 'hardhat',
  },
  bolt: {
    name: 'Bolt',
    role: 'line robot',
    skin: '#cbd5e1', hair: '#64748b', shirt: '#334155', accent: '#22d3ee',
    hat: 'antenna',
  },
  mei: {
    name: 'Mei',
    role: 'process engineer',
    skin: '#e8b892', hair: '#111827', shirt: '#15803d', accent: '#fb7185',
    hat: 'goggles',
  },
}

const rand = (lo, hi) => lo + Math.random() * (hi - lo)

// Mounts only on a real desktop viewport: wide enough for the rail to be a
// rail, and a pointer that can hover. Re-evaluated on resize so a window
// dragged narrow drops the character rather than squeezing it.
export function useDesktop() {
  const q = '(min-width: 1101px) and (hover: hover) and (pointer: fine)'
  const [on, setOn] = useState(() => window.matchMedia?.(q).matches ?? false)
  useEffect(() => {
    const m = window.matchMedia?.(q)
    if (!m) return
    const f = e => setOn(e.matches)
    m.addEventListener('change', f)
    return () => m.removeEventListener('change', f)
  }, [])
  return on
}

// Types `text` out one character at a time; returns what has been typed.
function useTypewriter(text, speed = 28) {
  const [n, setN] = useState(0)
  useEffect(() => {
    setN(0)
    if (!text) return
    const t = setInterval(() => setN(k => {
      if (k >= text.length) { clearInterval(t); return k }
      return k + 1
    }), speed)
    return () => clearInterval(t)
  }, [text, speed])
  return text.slice(0, n)
}

// Idle gesture scheduler. Each gesture is a state patch plus how long it
// holds; the effect re-arms itself with a fresh random delay after each one so
// the rhythm never repeats. Returns the pose the SVG should render.
function useIdleLoop(mood, reduced) {
  const [pose, setPose] = useState({ blink: false, tilt: 0, gazeX: 0, gazeY: 0, nod: 0 })
  useEffect(() => {
    if (reduced) return
    let alive = true
    const timers = []
    const later = (ms, fn) => { const t = setTimeout(() => alive && fn(), ms); timers.push(t) }

    // Blinks are independent of everything else: people blink while talking.
    const blink = () => {
      setPose(p => ({ ...p, blink: true }))
      later(120, () => setPose(p => ({ ...p, blink: false })))
      // Occasional double blink.
      if (Math.random() < 0.2) later(260, () => {
        setPose(p => ({ ...p, blink: true }))
        later(120, () => setPose(p => ({ ...p, blink: false })))
      })
      later(rand(2200, 6500), blink)
    }
    later(rand(600, 2500), blink)

    // Larger gestures wander only while idle; other moods pin the pose.
    const gesture = () => {
      if (mood === 'idle') {
        const r = Math.random()
        if (r < 0.35) {
          // glance somewhere, hold, return
          setPose(p => ({ ...p, gazeX: rand(-1, 1), gazeY: rand(-0.6, 0.6) }))
          later(rand(700, 1800), () => setPose(p => ({ ...p, gazeX: 0, gazeY: 0 })))
        } else if (r < 0.65) {
          // head tilt, hold, return
          setPose(p => ({ ...p, tilt: rand(-7, 7) }))
          later(rand(1200, 2600), () => setPose(p => ({ ...p, tilt: 0 })))
        } else if (r < 0.8) {
          // small nod
          setPose(p => ({ ...p, nod: 1 }))
          later(300, () => setPose(p => ({ ...p, nod: 0 })))
        } else {
          // tilt and glance together: the "hm?" look
          setPose(p => ({ ...p, tilt: rand(-9, 9), gazeX: rand(-1, 1), gazeY: -0.3 }))
          later(rand(1000, 2200), () => setPose(p => ({ ...p, tilt: 0, gazeX: 0, gazeY: 0 })))
        }
      }
      later(rand(2500, 6000), gesture)
    }
    later(rand(800, 2000), gesture)

    return () => { alive = false; timers.forEach(clearTimeout) }
  }, [mood, reduced])

  // Mood overrides, applied on top of the loop.
  return useMemo(() => {
    if (mood === 'thinking')  return { ...pose, tilt: 5, gazeX: 0.8, gazeY: -0.9, nod: 0 }
    if (mood === 'listening') return { ...pose, tilt: -3, gazeX: 0, gazeY: 0.15, nod: 0 }
    return pose
  }, [pose, mood])
}

// Mouth cycles between three shapes while speaking, then settles to a smile.
function useMouth(mood, reduced) {
  const [shape, setShape] = useState('smile')
  useEffect(() => {
    if (mood !== 'speaking' || reduced) { setShape(mood === 'thinking' ? 'flat' : 'smile'); return }
    const shapes = ['open', 'mid', 'smile', 'open', 'mid']
    let i = 0
    const t = setInterval(() => setShape(shapes[i++ % shapes.length]), 140)
    return () => clearInterval(t)
  }, [mood, reduced])
  return shape
}

function Hat({ kind, c }) {
  if (kind === 'hardhat') return (
    <g>
      <path d="M22 44 Q50 8 78 44 Z" fill={c.accent} />
      <rect x="16" y="42" width="68" height="7" rx="3.5" fill={c.accent} />
      <rect x="46" y="16" width="8" height="26" rx="4" fill="#eab308" opacity=".55" />
    </g>
  )
  if (kind === 'antenna') return (
    <g>
      <line x1="50" y1="26" x2="50" y2="10" stroke={c.hair} strokeWidth="3" strokeLinecap="round" />
      <circle cx="50" cy="8" r="4.5" fill={c.accent} className="av-antenna" />
      <rect x="24" y="24" width="52" height="12" rx="6" fill={c.hair} />
    </g>
  )
  if (kind === 'goggles') return (
    <g>
      <path d="M24 30 Q50 14 76 30 L76 40 L24 40 Z" fill={c.hair} />
      <rect x="27" y="30" width="46" height="8" rx="4" fill={c.accent} opacity=".9" />
      <rect x="31" y="31" width="16" height="6" rx="3" fill="#fff" opacity=".35" />
      <rect x="53" y="31" width="16" height="6" rx="3" fill="#fff" opacity=".35" />
    </g>
  )
  return null
}

function Eye({ cx, cy, pose, c, blink }) {
  // Pupils drift up to 3.5px with the gaze vector; the lid is a rect that
  // scales down over the eyeball on blink.
  const px = cx + pose.gazeX * 3.5
  const py = cy + pose.gazeY * 2.5
  return (
    <g>
      <ellipse cx={cx} cy={cy} rx="6.5" ry="7" fill="#fff" />
      <circle cx={px} cy={py} r="3.6" fill="#1f2937" className="av-pupil" />
      <circle cx={px - 1.2} cy={py - 1.4} r="1.1" fill="#fff" />
      <rect x={cx - 7} y={cy - 8} width="14" height="16" rx="6" fill={c.skin}
            className="av-lid" style={{ transform: `scaleY(${blink ? 1 : 0})` }} />
    </g>
  )
}

function Mouth({ shape }) {
  const d = {
    smile: 'M42 68 Q50 75 58 68',
    flat:  'M43 69 L57 69',
    mid:   'M43 68 Q50 73 57 68 Q50 74 43 68',
    open:  'M43 66 Q50 64 57 66 Q57 78 50 79 Q43 78 43 66',
  }[shape]
  const filled = shape === 'open' || shape === 'mid'
  return <path d={d} className="av-mouth" fill={filled ? '#7f1d1d' : 'none'}
               stroke="#7f1d1d" strokeWidth="2.2" strokeLinecap="round" />
}

export function AvatarFigure({ character = 'ada', mood = 'idle', size = 96 }) {
  const c = CHARACTERS[character] || CHARACTERS.ada
  const reduced = useMemo(() =>
    typeof window !== 'undefined' &&
    window.matchMedia?.('(prefers-reduced-motion: reduce)').matches, [])
  const pose = useIdleLoop(mood, reduced)
  const mouth = useMouth(mood, reduced)
  const browLift = mood === 'thinking' ? -3 : 0

  return (
    <svg className={`avatar avatar-${mood}`} width={size} height={size} viewBox="0 0 100 100"
         role="img" aria-label={`${c.name}, ${c.role}`}>
      {/* body breathes via CSS; the head group tilts and nods via transform */}
      <g className="av-body">
        <path d="M22 100 Q22 78 50 78 Q78 78 78 100 Z" fill={c.shirt} />
        <rect x="44" y="70" width="12" height="12" rx="4" fill={c.skin} />
      </g>
      <g className="av-head"
         style={{ transform: `rotate(${pose.tilt}deg) translateY(${pose.nod * 2.5}px)` }}>
        <ellipse cx="50" cy="50" rx="27" ry="29" fill={c.skin} />
        {c.hat !== 'antenna' && <path d="M23 46 Q30 22 50 22 Q70 22 77 46 Q66 36 50 36 Q34 36 23 46 Z" fill={c.hair} />}
        <Hat kind={c.hat} c={c} />
        {/* brows */}
        <path d={`M32 ${41 + browLift} Q38 ${37 + browLift} 44 ${41 + browLift}`} stroke={c.hair}
              strokeWidth="2.4" fill="none" strokeLinecap="round" className="av-brow" />
        <path d={`M56 ${41 + browLift} Q62 ${37 + browLift} 68 ${41 + browLift}`} stroke={c.hair}
              strokeWidth="2.4" fill="none" strokeLinecap="round" className="av-brow" />
        <Eye cx={38} cy={50} pose={pose} c={c} blink={pose.blink} />
        <Eye cx={62} cy={50} pose={pose} c={c} blink={pose.blink} />
        {/* cheeks */}
        <circle cx="31" cy="60" r="4" fill="#fb7185" opacity=".25" />
        <circle cx="69" cy="60" r="4" fill="#fb7185" opacity=".25" />
        <Mouth shape={mouth} />
      </g>
    </svg>
  )
}

// Suggested questions for the current view. The list is deliberately short
// and specific to what is on screen: the open tool, the tab, the tools that
// are actually down right now. Generic fallbacks fill any gaps.
export function suggestionsFor({ tab, openTool, openProduct, offline = [], cohort } = {}) {
  // The two app questions lead on every page: the assistant knows the
  // dashboard, and these are what a new engineer asks first.
  const out = ['What can I do on this page?', 'What is this page telling me?']
  const down = offline[0]
  if (openTool) {
    out.push(`What is ${openTool} working on right now?`)
    out.push(`What happens if ${openTool} goes down?`)
    out.push(`Is ${openTool} a bottleneck?`)
  }
  if (down) {
    out.push(`What is the impact of ${down} being down?`)
    if (offline.length > 1) out.push(`Which of the ${offline.length} tools down hurts most?`)
  }
  switch (tab) {
    case 'lots':
      out.push(cohort ? `How is cohort ${cohort} tracking?` : 'Which cohort is furthest behind?')
      out.push('Which lots are late right now?')
      out.push('Why are lots sitting unassigned?')
      break
    case 'tools':
      out.push('Which tool is the bottleneck?')
      out.push('Which tools are idle while lots wait?')
      break
    case 'floor':
      out.push('Which bay is the busiest?')
      out.push('Where is WIP piling up on the floor?')
      break
    case 'routes':
      out.push(openProduct ? `Where does ${openProduct} spend most of its queue time?`
                           : 'Which route step has the longest queue?')
      out.push('Which product is slipping its due dates?')
      break
    case 'slate':
      out.push('What is on the slate for the next shift?')
      out.push('Which dispatch decisions were overridden?')
      break
    case 'results':
      out.push('Which rule wins on on-time delivery?')
      out.push('How much did the optimizer improve cycle time?')
      break
    case 'topology':
      out.push('What does the API read from Kafka?')
      out.push('Can anything on this page change the fab?')
      break
    default:
      out.push('What is the fab doing right now?')
      out.push('Which tool is the bottleneck?')
      out.push('Why are lots sitting unassigned?')
  }
  out.push('What happens if the busiest tool goes down?')
  return [...new Set(out)].slice(0, 8)
}

// The character plus its speech bubble. `mood` comes from the chat panel;
// `context` from the router; `onAsk` sends a suggestion into the chat.
export default function Avatar({ mood = 'idle', context, onAsk, busy }) {
  const [character, setCharacter] = useState(() => {
    try { return localStorage.getItem('avatarCharacter') || 'ada' } catch { return 'ada' }
  })
  useEffect(() => {
    try { localStorage.setItem('avatarCharacter', character) } catch { /* ignore */ }
  }, [character])
  const c = CHARACTERS[character] || CHARACTERS.ada

  // The parent rebuilds `context` every render; key on its content so the
  // rotation only resets when the view actually changes.
  const ctxKey = JSON.stringify(context || {})
  const all = useMemo(() => suggestionsFor(JSON.parse(ctxKey)), [ctxKey])
  // Three visible at a time, rotating through the pool so the bubble feels
  // alive without being noisy; reset when the view changes.
  const [offset, setOffset] = useState(0)
  useEffect(() => { setOffset(0) }, [all])
  useEffect(() => {
    if (all.length <= 3 || busy) return
    const t = setInterval(() => setOffset(o => (o + 3) % all.length), 14000)
    return () => clearInterval(t)
  }, [all, busy])
  const shown = useMemo(() => {
    const r = []
    for (let i = 0; i < Math.min(3, all.length); i++) r.push(all[(offset + i) % all.length])
    return r
  }, [all, offset])

  const heading = mood === 'thinking' ? 'Checking the live state…'
    : mood === 'speaking' ? 'Here is what I found.'
    : mood === 'listening' ? 'Go ahead, I am listening.'
    : `Hi, I'm ${c.name}. You could ask:`

  return (
    <div className={`avatar-wrap avatar-wrap-${mood}`}>
      <div className="avatar-figure">
        <AvatarFigure character={character} mood={mood} />
        <div className="avatar-pick" role="group" aria-label="Choose a character">
          {Object.entries(CHARACTERS).map(([k, v]) => (
            <button key={k} type="button" title={`${v.name}, ${v.role}`}
                    className={k === character ? 'on' : ''}
                    style={{ background: v.shirt }}
                    onClick={() => setCharacter(k)} aria-pressed={k === character} />
          ))}
        </div>
      </div>
      <div className="avatar-bubble">
        <div className="avatar-say">{heading}</div>
        {mood !== 'thinking' && (
          <div className="avatar-qs" key={offset}>
            {shown.map(q => (
              <button key={q} type="button" className="avatar-q" disabled={busy}
                      onClick={() => onAsk?.(q)}>{q}</button>
            ))}
          </div>
        )}
        {mood === 'thinking' && <div className="avatar-dots"><i /><i /><i /></div>}
      </div>
    </div>
  )
}


// ---------------------------------------------------------------------------
// Corner launcher. Lives in the bottom-right of the viewport while the rail is
// closed: a round badge with the character and, beside it, a speech bubble
// that types out a greeting and offers one question for the current page.
// It appears just after first paint (not in the same frame as the page), so
// the page lands first and the character arrives as a second beat.
// Clicking the character opens the rail; clicking the question opens the rail
// and asks it. The bubble can be dismissed for the session; the badge stays.
// ---------------------------------------------------------------------------
export const LAUNCHER_DELAY_MS = 800

export function AvatarLauncher({ context, onOpen, onAsk, delay = LAUNCHER_DELAY_MS }) {
  const desktop = useDesktop()
  const [shown, setShown] = useState(false)
  const [bubble, setBubble] = useState(() => {
    try { return sessionStorage.getItem('launcherBubble') !== '0' } catch { return true }
  })
  const [hover, setHover] = useState(false)
  const [character] = useState(() => {
    try { return localStorage.getItem('avatarCharacter') || 'ada' } catch { return 'ada' }
  })
  const c = CHARACTERS[character] || CHARACTERS.ada

  useEffect(() => {
    const t = setTimeout(() => setShown(true), delay)
    return () => clearTimeout(t)
  }, [delay])

  const ctxKey = JSON.stringify(context || {})
  const all = useMemo(() => suggestionsFor(JSON.parse(ctxKey)), [ctxKey])
  // One question at a time, rotating slowly; the two app questions lead the
  // pool so a fresh visitor sees "what can I do on this page" first.
  const [i, setI] = useState(0)
  useEffect(() => { setI(0) }, [ctxKey])
  useEffect(() => {
    if (!shown || !bubble) return
    const t = setInterval(() => setI(k => (k + 1) % all.length), 18000)
    return () => clearInterval(t)
  }, [shown, bubble, all.length])
  const question = all[i % all.length]
  const typed = useTypewriter(shown && bubble
    ? `Hi! I'm ${c.name}. Need a hand with this page, or want to know what the fab is doing? Ask me anything, for example:` : '')

  const dismiss = () => {
    setBubble(false)
    try { sessionStorage.setItem('launcherBubble', '0') } catch { /* ignore */ }
  }

  if (!desktop || !shown) return null
  return (
    <div className="launcher" role="complementary" aria-label="Assistant">
      {bubble && (
        <div className="launcher-bubble">
          <button type="button" className="launcher-close" onClick={dismiss}
                  title="Hide" aria-label="Hide this message">&times;</button>
          <div className="launcher-text">{typed}<span className="launcher-caret" /></div>
          {typed.length > 40 && (
            <button type="button" className="launcher-q" onClick={() => onAsk?.(question)}>
              {question}
            </button>
          )}
        </div>
      )}
      <button type="button" className="launcher-badge" onClick={onOpen}
              onMouseEnter={() => setHover(true)} onMouseLeave={() => setHover(false)}
              title="Open the assistant" aria-label="Open the assistant">
        <AvatarFigure character={character} mood={hover ? 'listening' : 'idle'} size={64} />
      </button>
    </div>
  )
}
