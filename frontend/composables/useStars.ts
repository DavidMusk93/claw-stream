import type { Star } from '~/types/api'

export function useStars() {
  const config = useRuntimeConfig()

  // Client-only fetch: SSR must not block on /api/stars. If the backend is
  // slow or mid-restart, an awaited SSR fetch leaves the browser with no HTML
  // at all (white screen); with server:false the shell + skeleton render
  // instantly and data fills in when the client fetch completes.
  const { data: stars, pending, error, refresh } = useFetch<Star[]>('/api/stars', {
    baseURL: config.public.apiBase,
    key: 'stars',
    server: false,
  })

  // Retry once on the client when the fetch failed (e.g. backend mid-restart).
  if (import.meta.client) {
    onMounted(() => {
      if (error.value) {
        refresh()
      }
    })
  }

  return {
    stars,
    pending,
    error,
  }
}
