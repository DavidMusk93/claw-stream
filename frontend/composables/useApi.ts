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
    syncTraceIdFromResponse(res)
    return res._data
  }

  async function getTorrentStatus(hash: string) {
    const res = await $fetch.raw(`/torrent/status/${hash}`, {
      baseURL: config.public.apiBase,
      headers: _headers(),
    })
    syncTraceIdFromResponse(res)
    return res._data
  }

  async function addTorrent(magnet: string) {
    const res = await $fetch.raw('/torrent/add', {
      baseURL: config.public.apiBase,
      method: 'POST',
      headers: _headers(),
      body: { magnet },
    })
    syncTraceIdFromResponse(res)
    return res._data
  }

  async function getCacheMetrics() {
    const res = await $fetch.raw('/api/cache/metrics', {
      baseURL: config.public.apiBase,
      headers: _headers(),
    })
    syncTraceIdFromResponse(res)
    return res._data
  }

  async function getCacheItems() {
    const res = await $fetch.raw('/api/cache', {
      baseURL: config.public.apiBase,
      headers: _headers(),
    })
    syncTraceIdFromResponse(res)
    return res._data
  }

  async function deleteCache(hash: string) {
    const res = await $fetch.raw(`/api/cache/${hash}`, {
      baseURL: config.public.apiBase,
      method: 'DELETE',
      headers: _headers(),
    })
    syncTraceIdFromResponse(res)
    return res._data
  }

  async function deleteStar(code: string) {
    const res = await $fetch.raw(`/api/stars/${code}`, {
      baseURL: config.public.apiBase,
      method: 'DELETE',
      headers: _headers(),
    })
    syncTraceIdFromResponse(res)
    return res._data
  }

  async function likeTitle(code: string, liked: boolean) {
    const res = await $fetch.raw('/api/stars/like', {
      baseURL: config.public.apiBase,
      method: 'POST',
      headers: _headers(),
      body: { code, liked },
    })
    syncTraceIdFromResponse(res)
    return res._data
  }

  async function getSyncStatus() {
    const res = await $fetch.raw('/api/stars/sync', {
      baseURL: config.public.apiBase,
      headers: _headers(),
    })
    syncTraceIdFromResponse(res)
    return res._data as { running: boolean; elapsed?: number; last_error?: string | null }
  }

  return {
    checkStream,
    getTorrentStatus,
    addTorrent,
    getCacheMetrics,
    getCacheItems,
    deleteCache,
    deleteStar,
    likeTitle,
    getSyncStatus,
  }
}
