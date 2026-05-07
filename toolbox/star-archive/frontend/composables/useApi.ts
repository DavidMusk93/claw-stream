export function useApi() {
  const config = useRuntimeConfig()

  async function checkStream(hash: string) {
    return $fetch(`/api/check/${hash}`, {
      baseURL: config.public.apiBase,
    })
  }

  async function getTorrentStatus(hash: string) {
    return $fetch(`/torrent/status/${hash}`, {
      baseURL: config.public.apiBase,
    })
  }

  async function addTorrent(magnet: string) {
    return $fetch('/torrent/add', {
      baseURL: config.public.apiBase,
      method: 'POST',
      body: { magnet },
    })
  }

  async function getCacheMetrics() {
    return $fetch('/api/cache/metrics', {
      baseURL: config.public.apiBase,
    })
  }

  async function getCacheItems() {
    return $fetch('/api/cache', {
      baseURL: config.public.apiBase,
    })
  }

  async function deleteCache(hash: string) {
    return $fetch(`/api/cache/${hash}`, {
      baseURL: config.public.apiBase,
      method: 'DELETE',
    })
  }

  return {
    checkStream,
    getTorrentStatus,
    addTorrent,
    getCacheMetrics,
    getCacheItems,
    deleteCache,
  }
}
