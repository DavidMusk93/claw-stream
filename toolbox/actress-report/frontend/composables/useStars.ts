export function useStars() {
  const config = useRuntimeConfig()

  const { data: stars, pending, error } = useFetch('/api/stars', {
    baseURL: config.public.apiBase,
    key: 'stars',
  })

  return {
    stars,
    pending,
    error,
  }
}
