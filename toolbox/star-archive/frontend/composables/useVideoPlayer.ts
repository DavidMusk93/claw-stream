import type { TorrentStatus } from '~/types/api'
import { logInfo, logError } from './useLogger'

export function useVideoPlayer() {
  const config = useRuntimeConfig()
  const status = ref<TorrentStatus | null>(null)
  const loading = ref(false)
  const error = ref('')
  const canplayFired = ref(false)
  let pollTimer: ReturnType<typeof setInterval> | null = null

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
    logInfo('player', `startPolling ${hash.slice(0, 12)}`)
    pollTimer = setInterval(async () => {
      try {
        await pollStatus(hash)
      } catch {
        // ignore polling errors, already logged
      }
    }, 2000)
  }

  function stopPolling() {
    if (pollTimer) {
      logInfo('player', 'stopPolling')
      clearInterval(pollTimer)
      pollTimer = null
    }
  }

  async function waitForHeadReady(hash: string, timeoutSec = 180): Promise<boolean> {
    loading.value = true
    error.value = ''
    const start = Date.now()
    logInfo('player', `waitForHeadReady ${hash.slice(0, 12)} start`)

    while (Date.now() - start < timeoutSec * 1000) {
      if (await checkHeadReady(hash)) {
        loading.value = false
        logInfo('player', `waitForHeadReady ${hash.slice(0, 12)} success in ${((Date.now() - start) / 1000).toFixed(1)}s`)
        return true
      }
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
      await new Promise(r => setTimeout(r, 1500))
    }

    loading.value = false
    error.value = '加载超时，请检查文件完整性'
    logError('player', `waitForHeadReady ${hash.slice(0, 12)} timeout after ${timeoutSec}s`)
    return false
  }

  async function reportSeek(hash: string, time: number, duration: number) {
    if (!hash || !duration || duration === Infinity) return
    try {
      await $fetch('/torrent/seek', {
        baseURL: config.public.apiBase,
        method: 'POST',
        headers: { 'x-trace-id': localStorage.getItem('claw_trace_id') || '' },
        body: { hash, time, duration },
      })
    } catch (e: any) {
      // silent: seek reporting is best-effort
    }
  }

  async function reportProgress(hash: string, time: number, duration: number) {
    if (!hash || !duration || duration === Infinity) return
    try {
      await $fetch('/torrent/progress', {
        baseURL: config.public.apiBase,
        method: 'POST',
        headers: { 'x-trace-id': localStorage.getItem('claw_trace_id') || '' },
        body: { hash, time, duration },
      })
    } catch (e: any) {
      // silent: progress reporting is best-effort
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
    formatSpeed,
  }
}
