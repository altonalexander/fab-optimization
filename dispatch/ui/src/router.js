import { useCallback, useEffect, useState } from 'react'

// ---------------------------------------------------------------------------
// Hash routing. Deliberately hand-rolled and ~80 lines: this app is a single
// page served by vite's dev server and by a static bundle behind the API, so a
// history-API router would need server-side rewrites that neither provides.
// Hashes work unchanged in both, and every view stays copy-pasteable as a URL.
//
//   #/live                       #/tools?q=ETCH&type=ETCH
//   #/tools/ETCH_11              #/floor?bay=3,2&heat=1
// ---------------------------------------------------------------------------

export const TABS = ['live', 'lots', 'tools', 'floor', 'routes', 'slate', 'results', 'scenario', 'topology']
const DEFAULT = '/live'

export function parseHash(hash = window.location.hash) {
  const raw = String(hash || '').replace(/^#/, '')
  if (!raw || raw === '/') return { path: DEFAULT, segments: DEFAULT.slice(1).split('/'), query: {} }
  const qi = raw.indexOf('?')
  const path = qi === -1 ? raw : raw.slice(0, qi)
  const query = {}
  if (qi !== -1) {
    for (const [k, v] of new URLSearchParams(raw.slice(qi + 1))) query[k] = v
  }
  const norm = path.startsWith('/') ? path : `/${path}`
  return {
    path: norm,
    segments: norm.slice(1).split('/').filter(Boolean).map(decodeURIComponent),
    query,
  }
}

// Empty/false-ish values are dropped so a link never carries `?q=&type=all`
// noise -- the shortest URL that reproduces the view is the one people paste.
// `path` may be a string ("/tools/ETCH_11") or an array of raw segments
// (["tools", id]). Prefer the array whenever a segment is data: a string path
// cannot distinguish a separator from a slash inside an id.
export function buildHash(path, query = {}) {
  const segs = Array.isArray(path)
    ? path
    : String(path).split('/').filter(Boolean).map(decodeURIComponent)
  const qs = new URLSearchParams()
  for (const [k, v] of Object.entries(query)) {
    if (v === undefined || v === null || v === '' || v === false) continue
    qs.set(k, v === true ? '1' : String(v))
  }
  const s = qs.toString()
  return `#/${segs.map(encodeURIComponent).join('/')}${s ? `?${s}` : ''}`
}

export function navigate(path, query = {}, { replace = false } = {}) {
  const h = buildHash(path, query)
  if (h === window.location.hash) return
  if (replace) window.history.replaceState(null, '', h)
  else window.location.hash = h
  if (replace) window.dispatchEvent(new HashChangeEvent('hashchange'))
}

export function useRoute() {
  const [route, setRoute] = useState(() => parseHash())

  useEffect(() => {
    const onChange = () => setRoute(parseHash())
    window.addEventListener('hashchange', onChange)
    // A bare "/" is stamped into the bar on first load so the back button has
    // somewhere to return to instead of dropping out of the app entirely.
    if (!window.location.hash) navigate(DEFAULT, {}, { replace: true })
    return () => window.removeEventListener('hashchange', onChange)
  }, [])

  // Merge-style query update, so one control can change its own key without
  // knowing which other filters happen to be in the URL.
  const setQuery = useCallback((patch, opts) => {
    const cur = parseHash()
    navigate(cur.path, { ...cur.query, ...patch }, { replace: true, ...opts })
  }, [])

  return { ...route, navigate, setQuery }
}

export const linkTo = buildHash
