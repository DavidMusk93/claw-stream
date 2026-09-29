/* Global auth guard: redirect to login when xstream_auth=ok cookie is missing */
export default defineNuxtRouteMiddleware((to) => {
  // Login page itself is exempt from auth
  if (to.path === '/login') return

  const auth = useCookie('xstream_auth', { maxAge: 86400 })
  if (auth.value !== 'ok') {
    return navigateTo('/login')
  }
})
