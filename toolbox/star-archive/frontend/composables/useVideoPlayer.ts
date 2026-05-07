import type { TorrentStatus } from '~/types/api'

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
      }) as any
      const ok = res.head_ready === true
      console.log(`[player] checkHeadReady ${hash.slice(0, 12)} -> ${ok} (${(performance.now() - t0).toFixed(0)}ms)`)
      return ok
    } catch (e: any) {
      console.error(`[player] checkHeadReady ${hash.slice(0, 12)} failed:`, e.message || e)
      return false
    }
  }

  async function pollStatus(hash: string) {
    try {
      const res = await $fetch(`/torrent/status/${hash}`, {
        baseURL: config.public.apiBase,
      }) as TorrentStatus
      const prev = status.value
      status.value = res
      // Log state transitions
      if (!prev?.head_ready && res.head_ready) {
        console.log(`[player] status ${hash.slice(0, 12)} head_ready=true progress=${res.progress.toFixed(1)}%`)
      }
      return res
    } catch (e: any) {
      console.error(`[player] pollStatus ${hash.slice(0, 12)} failed:`, e.message || e)
      throw e
    }
  }

  function startPolling(hash: string) {
    stopPolling()
    console.log(`[player] startPolling ${hash.slice(0, 12)}`)
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
      console.log('[player] stopPolling')
      clearInterval(pollTimer)
      pollTimer = null
    }
  }

  async function waitForHeadReady(hash: string, timeoutSec = 180): Promise<boolean> {
    loading.value = true
    error.value = ''
    const start = Date.now()
    console.log(`[player] waitForHeadReady ${hash.slice(0, 12)} start`)

    while (Date.now() - start < timeoutSec * 1000) {
      if (await checkHeadReady(hash)) {
        loading.value = false
        console.log(`[player] waitForHeadReady ${hash.slice(0, 12)} success in ${((Date.now() - start) / 1000).toFixed(1)}s`)
        return true
      }
      // try to add torrent if not present
      try {
        await $fetch('/torrent/add', {
          baseURL: config.public.apiBase,
          method: 'POST',
          body: { magnet: `magnet:?xt=urn:btih:${hash}` },
        })
      } catch {
        // already added or other error
      }
      await new Promise(r => setTimeout(r, 1500))
    }

    loading.value = false
    error.value = '加载超时，请检查文件完整性'
    console.error(`[player] waitForHeadReady ${hash.slice(0, 12)} timeout after ${timeoutSec}s`)
    return false
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
    formatSpeed,
  }
}
