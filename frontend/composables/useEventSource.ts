/**
 * useEventSource — Global SSE connection manager
 *
 * Single EventSource replaces all polling:
 *   sync.started / sync.progress / sync.completed / sync.error
 *   star.ready
 *   torrent.status (future)
 *   cache.update (future)
 *
 * Auto-reconnects on disconnect with exponential backoff.
 */

import { logInfo, logError, logWarn } from './useLogger'

type EventHandler = (data: any) => void

const listeners = new Map<string, Set<EventHandler>>()
let es: EventSource | null = null
let reconnectTimer: ReturnType<typeof setTimeout> | null = null
let reconnectDelay = 1000
let manuallyClosed = false
// logError reports to /api/log over HTTP — during a backend restart every
// reconnect attempt would POST to the very backend that is down. Report the
// first failure as an error, subsequent retries as local-only warnings.
let errorReported = false
const MAX_RECONNECT_DELAY = 30000

function getListeners(event: string): Set<EventHandler> {
  if (!listeners.has(event)) {
    listeners.set(event, new Set())
  }
  return listeners.get(event)!
}

function hasAnyListeners(): boolean {
  for (const set of listeners.values()) {
    if (set.size > 0) return true
  }
  return false
}

function connect() {
  if (typeof window === 'undefined') return
  if (es?.readyState === EventSource.OPEN || es?.readyState === EventSource.CONNECTING) return
  if (manuallyClosed) return

  const url = '/api/events'
  logInfo('sse', `connecting to ${url}`)

  es = new EventSource(url, { withCredentials: true })

  es.onopen = () => {
    logInfo('sse', 'connected')
    reconnectDelay = 1000
    errorReported = false
  }

  es.onmessage = (e) => {
    // Comment lines (":heartbeat") never fire onmessage, so no filter needed.
    if (!e.data) return
    try {
      const payload = JSON.parse(e.data)
      const handlers = getListeners(payload.event)
      handlers.forEach((fn) => {
        try { fn(payload.data) } catch (err) { /* ignore handler errors */ }
      })
    } catch {
      // ignore malformed JSON
    }
  }

  es.onerror = () => {
    if (manuallyClosed) return
    if (errorReported) {
      logWarn('sse', `still disconnected, reconnecting in ${reconnectDelay}ms`)
    } else {
      logError('sse', `connection error, reconnecting in ${reconnectDelay}ms`)
      errorReported = true
    }
    es?.close()
    es = null
    if (reconnectTimer) clearTimeout(reconnectTimer)
    reconnectTimer = setTimeout(() => {
      reconnectDelay = Math.min(reconnectDelay * 2, MAX_RECONNECT_DELAY)
      connect()
    }, reconnectDelay)
  }
}

export function onServerEvent(event: string, handler: EventHandler): () => void {
  getListeners(event).add(handler)
  manuallyClosed = false
  connect()
  return () => {
    getListeners(event).delete(handler)
    if (!hasAnyListeners()) {
      closeEventSource()
    }
  }
}

export function closeEventSource() {
  manuallyClosed = true
  if (reconnectTimer) clearTimeout(reconnectTimer)
  reconnectTimer = null
  es?.close()
  es = null
}

/** Composable wrapper for auto-import compatibility */
export function useEventSource() {
  return { onServerEvent, closeEventSource }
}
