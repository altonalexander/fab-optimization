// Markdown-lite for assistant replies: paragraphs, bullet lists, **bold**,
// `code`, and links. Rendered as React nodes, never as HTML, so a reply can
// not inject markup. Links are kept only when they stay inside the app
// (hash routes) or the API docs; anything else renders as plain text.

const INLINE = /(\*\*[^*]+\*\*|`[^`]+`|\[[^\]]+\]\([^)]+\)|#\/[\w\-./?=&%,]+)/g

function safeHref(h) {
  return /^(#\/|\/docs)/.test(h) ? h : null
}

function inline(text, keyBase) {
  const out = []
  let last = 0, m, i = 0
  INLINE.lastIndex = 0
  while ((m = INLINE.exec(text))) {
    if (m.index > last) out.push(text.slice(last, m.index))
    const tok = m[0]
    const k = `${keyBase}-${i++}`
    if (tok.startsWith('**')) out.push(<b key={k}>{tok.slice(2, -2)}</b>)
    else if (tok.startsWith('`')) out.push(<code key={k}>{tok.slice(1, -1)}</code>)
    else if (tok.startsWith('[')) {
      const mm = /^\[([^\]]+)\]\(([^)]+)\)$/.exec(tok)
      const href = mm && safeHref(mm[2].trim())
      out.push(href ? <a key={k} href={href}>{mm[1]}</a> : (mm ? mm[1] : tok))
    } else out.push(<a key={k} href={tok}>{tok}</a>)
    last = m.index + tok.length
  }
  if (last < text.length) out.push(text.slice(last))
  return out
}

export default function Md({ text }) {
  const blocks = []
  let list = null
  const lines = String(text || '').split('\n')
  lines.forEach((line, n) => {
    const li = /^\s*(?:[-*•]|\d+[.)])\s+(.*)$/.exec(line)
    if (li) {
      if (!list) { list = []; blocks.push(list) }
      list.push(<li key={n}>{inline(li[1], n)}</li>)
      return
    }
    list = null
    if (line.trim() === '') return
    const h = /^\s*#{1,6}\s+(.*)$/.exec(line)
    blocks.push(<p key={n}>{inline(h ? h[1] : line, n)}</p>)
  })
  return (
    <div className="md">
      {blocks.map((b, i) => Array.isArray(b) ? <ul key={i}>{b}</ul> : b)}
    </div>
  )
}
