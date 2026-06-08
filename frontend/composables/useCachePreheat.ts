/**
 * useCachePreheat — 页面加载后自动预热缓存
 *
 * 策略：每个 star 的作品按日期排序后，取第 1, 4, 7... 个（索引 0, 3, 6...）
 * 静默调用 /torrent/add 加入下载队列（不 prefetch，后台慢慢下载 metadata）
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
        // 已存在或其他错误，静默忽略
        logError('cache-preheat', `failed ${code}: ${e.message || e}`)
      }
      // 间隔 300ms 避免冲击后端
      await new Promise(r => setTimeout(r, 300))
    }

    logInfo('cache-preheat', 'preheat complete')
  }

  return { preheat }
}
