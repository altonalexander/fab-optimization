import { useCallback, useEffect, useState } from 'react'

// ---------------------------------------------------------------------------
// Path routing, hand-rolled and small. The view is the URL path:
//
//   /live                       /tools?q=ETCH&type=ETCH
//   /tools/ETCH_11              /floor?bay=3,2&heat=1
//
// Both places the bundle is served fall back to index.html for unknown
// paths (vite's dev server, and nginx's try_files), so no hash is needed.
// Plain paths also reach the server, which is what lets the login page send
// someone back to the exact view they asked for. Old "#/tools/..." links
// still work: they are rewritten to the path form on load.
// ---------------------------------------------------------------------------

export const TABS = ['live', 'lots', 'tools', 'floor', 'routes', 'slate', 'results', 'topology']
const DEFAULT = '/live'
const CHANGE = 'routechange'

export function parseUrl(pathname, search) {
  // Defaults read the browser only when nothing was passed, so the pure
  // form (tests, server-side) never touches `window`.
  if (pathname === undefined) {
    pathname = window.location.pathname
    if (search === undefined) search = window.location.search
  }
  let path = String(pathname || '/')
  if (!path.startsWith('/')) path = `/${path}`
  if (path === '/' || path === '') path = DEFAULT
  const query = {}
  for (const [k, v] of new URLSearchParams(search || '')) query[k] = v
  return {
    path,
    segments: path.slice(1).split('/').filter(Boolean).map(decodeURIComponent),
    query,
  }
}

// Empty/false-ish values are dropped so a link never carries `?q=&type=all`
// noise -- the shortest URL that reproduces the view is the one people paste.
// `path` may be a string ("/tools/ETCH_11") or an array of raw segments
// (["tools", id]). Prefer the array whenever a segment is data: a string path
// cannot distinguish a separator from a slash inside an id.
export function buildUrl(path, query = {}) {
  const segs = Array.isArray(path)
    ? path
    : String(path).split('/').filter(Boolean).map(decodeURIComponent)
  const qs = new URLSearchParams()
  for (const [k, v] of Object.entries(query)) {
    if (v === undefined || v === null || v === '' || v === false) continue
    qs.set(k, v === true ? '1' : String(v))
  }
  const s = qs.toString()
  return `/${segs.map(encodeURIComponent).join('/')}${s ? `?${s}` : ''}`
}

// Is this href one of the app's own views (as opposed to /admin, /auth/...,
// /docs, which are real server pages)?
export function isAppPath(href) {
  if (!href || !href.startsWith('/') || href.startsWith('//')) return false
  const first = href.slice(1).split(/[/?#]/)[0]
  return first === '' || TABS.includes(first)
}

export function navigate(path, query = {}, { replace = false } = {}) {
  const url = buildUrl(path, query)
  if (url === window.location.pathname + window.location.search) return
  if (replace) window.history.replaceState(null, '', url)
  else window.history.pushState(null, '', url)
  window.dispatchEvent(new Event(CHANGE))
}

export function useRoute() {
  const [route, setRoute] = useState(() => parseUrl())

  useEffect(() => {
    const onChange = () => setRoute(parseUrl())
    window.addEventListener('popstate', onChange)
    window.addEventListener(CHANGE, onChange)
    // In-app links are plain <a href="/tools/..."> so they copy and open in
    // new tabs like any link; a normal click is turned into a pushState so
    // the page does not reload and the live stream is not torn down.
    const onClick = (e) => {
      if (e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return
      const a = e.target.closest && e.target.closest('a[href]')
      if (!a || a.target || a.hasAttribute('download')) return
      const href = a.getAttribute('href')
      if (!isAppPath(href)) return
      e.preventDefault()
      const u = new URL(href, window.location.origin)
      navigate(u.pathname, Object.fromEntries(u.searchParams))
    }
    document.addEventListener('click', onClick)
    // Old-style hash links (#/tools/X) from bookmarks and chat: rewrite once.
    const h = window.location.hash
    if (h.startsWith('#/')) {
      const u = new URL(h.slice(1), window.location.origin)
      navigate(u.pathname, Object.fromEntries(u.searchParams), { replace: true })
    } else if (window.location.pathname === '/') {
      // A bare "/" is stamped into the bar on first load so the back button
      // has somewhere to return to instead of dropping out of the app.
      navigate(DEFAULT, {}, { replace: true })
    }
    return () => {
      window.removeEventListener('popstate', onChange)
      window.removeEventListener(CHANGE, onChange)
      document.removeEventListener('click', onClick)
    }
  }, [])

  // Merge-style query update, so one control can change its own key without
  // knowing which other filters happen to be in the URL.
  const setQuery = useCallback((patch, opts) => {
    const cur = parseUrl()
    navigate(cur.path, { ...cur.query, ...patch }, { replace: true, ...opts })
  }, [])

  return { ...route, navigate, setQuery }
}

export const linkTo = buildUrl
