/**
 * useCachePreheat — Auto-preheat cache after page load
 *
 * Strategy: For each star's titles sorted by date, pick indices 0, 3, 6...
 * Silently call /torrent/add to join the download queue (no prefetch, background metadata download)
 */

import { logInfo, logError } from './useLogger'

export function useCachePreheat() {
  const config = useRuntimeConfig()

  async function preheat(stars: { titles?: { magnet?: string; code?: string }[] }[]) {
    const magnets: { code: string; magnet: string }[] = []

    for (const star of stars) {
      const titles = star.titles || []
      for (let i = 0; i < titles.length; i += 3) {
        const t = titles[i]
        if (t?.magnet && t.code) {
          magnets.push({ code: t.code, magnet: t.magnet })
        }
      }
    }

    if (magnets.length === 0) return

    logInfo('cache-preheat', `preheating ${magnets.length} titles`)

    for (const { code, magnet } of magnets) {
      try {
        await $fetch('/torrent/add', {
          baseURL: config.public.apiBase,
          method: 'POST',
          headers: { 'x-trace-id': localStorage.getItem('claw_trace_id') || '' },
          body: { magnet, prefetch: false },
        })
        logInfo('cache-preheat', `added ${code}`)
      } catch (e: any) {
        // Already exists or other error, silently ignore
        logError('cache-preheat', `failed ${code}: ${e.message || e}`)
      }
      // 300ms interval to avoid backend overload
      await new Promise(r => setTimeout(r, 300))
    }

    logInfo('cache-preheat', 'preheat complete')
  }

  return { preheat }
}
