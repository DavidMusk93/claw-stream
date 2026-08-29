/**
 * useTrack — User behavior tracking (埋点)
 *
 * Batches behavior events client-side and POSTs them to /api/track.
 * Fire-and-forget: tracking must never break or delay user actions.
 */

export interface TrackEvent {
  event: string
  code?: string
  star_code?: string
  meta?: Record<string, any>
}

const queue: TrackEvent[] = []
let flushTimer: ReturnType<typeof setTimeout> | null = null
let flushing = false
let pagehideBound = false

async function flush(apiBase: string) {
  if (flushing || queue.length === 0) return
  flushing = true
  const events = queue.splice(0, 100)
  try {
    await $fetch('/api/track', {
      baseURL: apiBase,
      method: 'POST',
      body: { events },
      keepalive: true,
    })
  } catch {
    // tracking is best-effort; drop on failure
  } finally {
    flushing = false
  }
}

export function useTrack() {
  const config = useRuntimeConfig()
  const apiBase = config.public.apiBase as string

  if (import.meta.client && !pagehideBound) {
    pagehideBound = true
    window.addEventListener('pagehide', () => { flush(apiBase) })
  }

  function track(event: string, opts: Omit<TrackEvent, 'event'> = {}) {
    if (!import.meta.client) return
    queue.push({ event, ...opts })
    if (queue.length >= 20) {
      flush(apiBase)
      return
    }
    if (flushTimer) clearTimeout(flushTimer)
    flushTimer = setTimeout(() => flush(apiBase), 2000)
  }

  return { track }
}
