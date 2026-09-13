import { Component } from 'react'

/**
 * One failing panel should not take the dashboard with it.
 *
 * React unmounts the whole tree when a render throws and nothing catches it,
 * so until now any error anywhere rendered a blank white page -- every tab,
 * the header, the assistant, all of it gone, with nothing on screen to say
 * what happened. Two such errors were found in one afternoon and both were
 * one-liners in a panel nobody was even looking at: ZoneInvariants reading
 * `.invariants` off an {"error": ...} payload from /api/zones, and ChatPanel
 * rendering a hook fewer once the assistant reported itself unavailable. The
 * fixes were small; the failure mode was total.
 *
 * A boundary per region turns that into a panel that says it failed while the
 * rest of the page keeps working. It is deliberately NOT one boundary at the
 * root -- that would catch the error and still blank the page, which is the
 * behaviour being replaced.
 *
 * `resetKey` is what makes this recoverable rather than sticky: React keeps a
 * boundary in its error state until it is re-mounted or told otherwise, so a
 * crashed tab would stay crashed even after navigating away and back. Passing
 * the current tab (or tool id) clears the error whenever that changes, so
 * moving on is the fix a viewer reaches for anyway.
 *
 * Boundaries catch render, lifecycle and constructor errors only. An error
 * thrown inside a fetch callback or an event handler is NOT caught here and
 * never was -- those still need their own handling at the call site.
 */
export default class ErrorBoundary extends Component {
  constructor(props) {
    super(props)
    this.state = { err: null }
  }

  static getDerivedStateFromError(err) {
    return { err }
  }

  componentDidUpdate(prev) {
    if (prev.resetKey !== this.props.resetKey && this.state.err) {
      this.setState({ err: null })
    }
  }

  componentDidCatch(err, info) {
    // Kept on the console in full. The panel below shows the message; the
    // component stack is what actually locates the bug, and throwing it away
    // to keep the UI tidy would be the wrong trade in the one place where
    // something has already gone wrong.
    console.error(`[${this.props.name || 'panel'}] render failed`, err, info)
  }

  render() {
    const { err } = this.state
    if (!err) return this.props.children
    const { name = 'This panel', compact = false } = this.props
    return (
      <div className={compact ? 'boundary boundary-compact' : 'boundary'} role="alert">
        <strong>{name} could not be drawn.</strong>
        <div className="muted">{String(err && err.message ? err.message : err)}</div>
        <div className="muted boundary-hint">
          The rest of the dashboard is unaffected. The full stack is in the
          browser console.
        </div>
        <button type="button" className="live" onClick={() => this.setState({ err: null })}>
          Try again
        </button>
      </div>
    )
  }
}
