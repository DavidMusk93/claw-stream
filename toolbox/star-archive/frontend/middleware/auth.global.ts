/* 全局认证守卫：无 claw_auth=ok cookie 时重定向到登录页 */
export default defineNuxtRouteMiddleware((to) => {
  // 登录页本身免认证
  if (to.path === '/login') return

  const auth = useCookie('claw_auth', { maxAge: 86400 })
  if (auth.value !== 'ok') {
    return navigateTo('/login')
  }
})
