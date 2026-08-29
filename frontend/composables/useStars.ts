import type { Star } from '~/types/api'

export function useStars() {
  const config = useRuntimeConfig()

  const { data: stars, pending, error, refresh } = useFetch<Star[]>('/api/stars', {
    baseURL: config.public.apiBase,
    key: 'stars',
  })

  // If the SSR fetch failed (e.g. backend mid-restart), the failure is baked
  // into the payload and the page would otherwise sit on the empty state until
  // a manual reload. Retry once on the client after mount.
  if (import.meta.client) {
    onMounted(() => {
      if (error.value || !stars.value) {
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
