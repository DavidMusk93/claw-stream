import type { TorrentStatus } from '~/types/api'

export function useVideoPlayer() {
  const config = useRuntimeConfig()
  const status = ref<TorrentStatus | null>(null)
  const loading = ref(false)
  const error = ref('')
  const canplayFired = ref(false)
  let pollTimer: ReturnType<typeof setInterval> | null = null

  async function checkHeadReady(hash: string): Promise<boolean> {
    const res = await $fetch(`/api/check/${hash}`, {
      baseURL: config.public.apiBase,
    }) as any
    return res.head_ready === true
  }

  async function pollStatus(hash: string) {
    const res = await $fetch(`/torrent/status/${hash}`, {
      baseURL: config.public.apiBase,
    }) as TorrentStatus
    status.value = res
    return res
  }

  function startPolling(hash: string) {
    stopPolling()
    pollTimer = setInterval(async () => {
      try {
        await pollStatus(hash)
      } catch (e) {
        // ignore
      }
    }, 2000)
  }

  function stopPolling() {
    if (pollTimer) {
      clearInterval(pollTimer)
      pollTimer = null
    }
  }

  async function waitForHeadReady(hash: string, timeoutSec = 180): Promise<boolean> {
    loading.value = true
    error.value = ''
    const start = Date.now()

    while (Date.now() - start < timeoutSec * 1000) {
      if (await checkHeadReady(hash)) {
        loading.value = false
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
