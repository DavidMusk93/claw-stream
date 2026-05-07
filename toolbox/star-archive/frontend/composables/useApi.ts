import { getTraceId, syncTraceIdFromResponse } from './useLogger'

function _headers() {
  return { 'x-trace-id': getTraceId() }
}

export function useApi() {
  const config = useRuntimeConfig()

  async function checkStream(hash: string) {
    const res = await $fetch.raw(`/api/check/${hash}`, {
      baseURL: config.public.apiBase,
      headers: _headers(),
    })
    syncTraceIdFromResponse(res.response)
    return res._data
  }

  async function getTorrentStatus(hash: string) {
    const res = await $fetch.raw(`/torrent/status/${hash}`, {
      baseURL: config.public.apiBase,
      headers: _headers(),
    })
    syncTraceIdFromResponse(res.response)
    return res._data
  }

  async function addTorrent(magnet: string) {
    const res = await $fetch.raw('/torrent/add', {
      baseURL: config.public.apiBase,
      method: 'POST',
      headers: _headers(),
      body: { magnet },
    })
    syncTraceIdFromResponse(res.response)
    return res._data
  }

  async function getCacheMetrics() {
    const res = await $fetch.raw('/api/cache/metrics', {
      baseURL: config.public.apiBase,
      headers: _headers(),
    })
    syncTraceIdFromResponse(res.response)
    return res._data
  }

  async function getCacheItems() {
    const res = await $fetch.raw('/api/cache', {
      baseURL: config.public.apiBase,
      headers: _headers(),
    })
    syncTraceIdFromResponse(res.response)
    return res._data
  }

  async function deleteCache(hash: string) {
    const res = await $fetch.raw(`/api/cache/${hash}`, {
      baseURL: config.public.apiBase,
      method: 'DELETE',
      headers: _headers(),
    })
    syncTraceIdFromResponse(res.response)
    return res._data
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
