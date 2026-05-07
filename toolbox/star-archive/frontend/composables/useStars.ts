import type { Star } from '~/types/api'

export function useStars() {
  const config = useRuntimeConfig()

  const { data: stars, pending, error } = useFetch<Star[]>('/api/stars', {
    baseURL: config.public.apiBase,
    key: 'stars',
  })

  return {
    stars,
    pending,
    error,
  }
}
