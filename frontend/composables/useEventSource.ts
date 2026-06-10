/**
 * useEventSource — Global SSE connection manager
 *
 * Single EventSource replaces all polling:
 *   sync.started / sync.completed / sync.error
 *   star.ready
 *   torrent.status (future)
 *   cache.update (future)
 *
 * Auto-reconnects on disconnect with exponential backoff.
 */

import { logInfo, logError } from './useLogger'

type EventHandler = (data: any) => void

const listeners = new Map<string, Set<EventHandler>>()
let es: EventSource | null = null
let reconnectTimer: ReturnType<typeof setTimeout> | null = null
let reconnectDelay = 1000
const MAX_RECONNECT_DELAY = 30000

function getListeners(event: string): Set<EventHandler> {
  if (!listeners.has(event)) {
    listeners.set(event, new Set())
  }
  return listeners.get(event)!
}

function connect() {
  if (typeof window === 'undefined') return
  if (es?.readyState === EventSource.OPEN || es?.readyState === EventSource.CONNECTING) return

  const url = '/api/events'
  logInfo('sse', `connecting to ${url}`)

  es = new EventSource(url)

  es.onopen = () => {
    logInfo('sse', 'connected')
    reconnectDelay = 1000
  }

  es.onmessage = (e) => {
    if (!e.data || e.data.startsWith(':heartbeat')) return
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
    logError('sse', `connection error, reconnecting in ${reconnectDelay}ms`)
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
  connect()
  return () => {
    getListeners(event).delete(handler)
  }
}

export function closeEventSource() {
  if (reconnectTimer) clearTimeout(reconnectTimer)
  es?.close()
  es = null
}
