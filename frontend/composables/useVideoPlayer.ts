import type { TorrentStatus } from '~/types/api'
import { logInfo, logError } from './useLogger'

export function useVideoPlayer() {
  const config = useRuntimeConfig()
  const status = ref<TorrentStatus | null>(null)
  const loading = ref(false)
  const error = ref('')
  const canplayFired = ref(false)
  let pollTimer: ReturnType<typeof setInterval> | null = null
  let _unsubEvent: (() => void) | null = null
  let _currentHash = ''

  async function checkHeadReady(hash: string): Promise<boolean> {
    const t0 = performance.now()
    try {
      const res = await $fetch(`/api/check/${hash}`, {
        baseURL: config.public.apiBase,
        headers: { 'x-trace-id': localStorage.getItem('claw_trace_id') || '' },
      }) as any
      const ok = res.head_ready === true
      logInfo('player', `checkHeadReady ${hash.slice(0, 12)} -> ${ok} (${(performance.now() - t0).toFixed(0)}ms)`)
      return ok
    } catch (e: any) {
      logError('player', `checkHeadReady ${hash.slice(0, 12)} failed: ${e.message || e}`)
      return false
    }
  }

  async function pollStatus(hash: string) {
    try {
      const res = await $fetch(`/torrent/status/${hash}`, {
        baseURL: config.public.apiBase,
        headers: { 'x-trace-id': localStorage.getItem('claw_trace_id') || '' },
      }) as TorrentStatus
      const prev = status.value
      status.value = res
      if (!prev?.head_ready && res.head_ready) {
        logInfo('player', `status ${hash.slice(0, 12)} head_ready=true progress=${res.progress.toFixed(1)}%`)
      }
      return res
    } catch (e: any) {
      logError('player', `pollStatus ${hash.slice(0, 12)} failed: ${e.message || e}`)
      throw e
    }
  }

  function startPolling(hash: string) {
    stopPolling()
    _currentHash = hash
    logInfo('player', `startPolling ${hash.slice(0, 12)}`)

    // SSE: instant push when torrent state changes
    const { onServerEvent } = useEventSource()
    _unsubEvent = onServerEvent('torrent.head_ready', (data: any) => {
      if (data.hash === hash) {
        logInfo('player', `SSE head_ready ${hash.slice(0, 12)}`)
        pollStatus(hash).catch(() => {})
      }
    })
    // Also listen on general status changes
    const unsubStatus = onServerEvent('torrent.status', (data: any) => {
      if (data.hash === hash) {
        pollStatus(hash).catch(() => {})
      }
    })
    _unsubEvent = () => {
      _unsubEvent?.()
      unsubStatus()
    }

    // Fallback polling every 5s (SSE covers instant changes)
    pollTimer = setInterval(async () => {
      try {
        await pollStatus(hash)
      } catch {
        // ignore polling errors, already logged
      }
    }, 5000)
  }

  function stopPolling() {
    if (pollTimer) {
      logInfo('player', 'stopPolling')
      clearInterval(pollTimer)
      pollTimer = null
    }
    if (_unsubEvent) {
      _unsubEvent()
      _unsubEvent = null
    }
    _currentHash = ''
  }

  async function waitForHeadReady(hash: string, timeoutSec = 180): Promise<boolean> {
    loading.value = true
    error.value = ''
    const start = Date.now()
    logInfo('player', `waitForHeadReady ${hash.slice(0, 12)} start`)

    // Ensure torrent is added once before polling.
    // add_torrent is idempotent; if already added it returns immediately.
    try {
      await $fetch('/torrent/add', {
        baseURL: config.public.apiBase,
        method: 'POST',
        headers: { 'x-trace-id': localStorage.getItem('claw_trace_id') || '' },
        body: { magnet: `magnet:?xt=urn:btih:${hash}` },
      })
    } catch {
      // already added or other error
    }

    // Trigger head+tail download so head_ready can be detected.
    try {
      await $fetch('/torrent/resume', {
        baseURL: config.public.apiBase,
        method: 'POST',
        headers: { 'x-trace-id': localStorage.getItem('claw_trace_id') || '' },
        body: { hash, time: 0, duration: 0 },
      })
    } catch {
      // ignore
    }

    while (Date.now() - start < timeoutSec * 1000) {
      if (await checkHeadReady(hash)) {
        loading.value = false
        logInfo('player', `waitForHeadReady ${hash.slice(0, 12)} success in ${((Date.now() - start) / 1000).toFixed(1)}s`)
        return true
      }
      await new Promise(r => setTimeout(r, 1500))
    }

    loading.value = false
    error.value = 'Load timeout, please check file integrity'
    logError('player', `waitForHeadReady ${hash.slice(0, 12)} timeout after ${timeoutSec}s`)
    return false
  }

  async function reportSeek(hash: string, time: number, duration: number) {
    if (!hash || !duration || duration === Infinity) return
    try {
      logInfo('player', `reportSeek ${hash.slice(0, 12)} time=${time.toFixed(1)}s`)
      await $fetch('/torrent/seek', {
        baseURL: config.public.apiBase,
        method: 'POST',
        headers: { 'x-trace-id': localStorage.getItem('claw_trace_id') || '' },
        body: { hash, time, duration },
      })
    } catch (e: any) {
      logError('player', `reportSeek ${hash.slice(0, 12)} failed: ${e.message || e}`)
    }
  }

  async function reportProgress(hash: string, time: number, duration: number) {
    if (!hash || !duration || duration === Infinity) return
    try {
      logInfo('player', `reportProgress ${hash.slice(0, 12)} time=${time.toFixed(1)}s/${duration.toFixed(1)}s`)
      await $fetch('/torrent/progress', {
        baseURL: config.public.apiBase,
        method: 'POST',
        headers: { 'x-trace-id': localStorage.getItem('claw_trace_id') || '' },
        body: { hash, time, duration },
      })
    } catch (e: any) {
      logError('player', `reportProgress ${hash.slice(0, 12)} failed: ${e.message || e}`)
    }
  }

  async function reportPause(hash: string) {
    if (!hash) return
    try {
      logInfo('player', `reportPause ${hash.slice(0, 12)}`)
      await $fetch('/torrent/pause', {
        baseURL: config.public.apiBase,
        method: 'POST',
        headers: { 'x-trace-id': localStorage.getItem('claw_trace_id') || '' },
        body: { hash, time: 0, duration: 0 },
      })
    } catch (e: any) {
      logError('player', `reportPause ${hash.slice(0, 12)} failed: ${e.message || e}`)
    }
  }

  async function reportResume(hash: string, time: number, duration: number) {
    if (!hash || !duration || duration === Infinity) return
    try {
      logInfo('player', `reportResume ${hash.slice(0, 12)} time=${time.toFixed(1)}s`)
      await $fetch('/torrent/resume', {
        baseURL: config.public.apiBase,
        method: 'POST',
        headers: { 'x-trace-id': localStorage.getItem('claw_trace_id') || '' },
        body: { hash, time, duration },
      })
    } catch (e: any) {
      logError('player', `reportResume ${hash.slice(0, 12)} failed: ${e.message || e}`)
    }
  }

  function formatSpeed(rate: number): string {
    if (rate > 1024 * 1024) return `${(rate / 1024 / 1024).toFixed(1)} MB/s`
    if (rate > 1024) return `${(rate / 1024).toFixed(1)} KB/s`
    return `${rate} B/s`
  }

  return {
    status,
    loading,
    error,
    canplayFired,
    checkHeadReady,
    pollStatus,
    startPolling,
    stopPolling,
    waitForHeadReady,
    reportSeek,
    reportProgress,
    reportPause,
    reportResume,
    formatSpeed,
  }
}
