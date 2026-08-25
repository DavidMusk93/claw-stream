/**
 * Never let the browser cache SSR HTML documents.
 *
 * Safari aggressively caches HTML without explicit Cache-Control and keeps
 * suspended tabs for days, serving a stale app shell (old JS chunks) after
 * deploys. Hashed /_nuxt/* assets are unaffected (Nitro sets its own
 * immutable header later, and they are not text/html requests).
 */
export default defineEventHandler((event) => {
  if (event.method !== 'GET') return
  const accept = getRequestHeader(event, 'accept') || ''
  if (accept.includes('text/html')) {
    setResponseHeader(event, 'Cache-Control', 'no-cache')
  }
})
