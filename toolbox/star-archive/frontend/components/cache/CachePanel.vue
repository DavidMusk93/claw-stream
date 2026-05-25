<template>
  <div class="fixed bottom-4 right-4 z-50">
    <!-- Toggle button -->
    <button
      class="w-12 h-12 rounded-full bg-[#1c1c1e] border border-white/[0.08] text-white flex items-center justify-center shadow-lg transition-all duration-200 active:scale-95 relative hover:border-white/20"
      @click="isOpen = !isOpen"
      title="缓存管理"
    >
      <svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
        <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/>
        <polyline points="17 8 12 3 7 8"/>
        <line x1="12" y1="3" x2="12" y2="15"/>
      </svg>
      <span v-if="activeCount > 0" class="absolute -top-0.5 -right-0.5 w-3 h-3 rounded-full bg-emerald-400 border-2 border-void shadow-[0_0_8px_rgba(52,211,153,0.4)]" />
    </button>

    <!-- Panel -->
    <Transition name="slide">
      <div
        v-if="isOpen"
        class="absolute bottom-14 right-0 w-[92vw] sm:w-[420px] max-h-[75vh] bg-[#1c1c1e] border border-white/[0.06] rounded-2xl shadow-2xl overflow-hidden flex flex-col"
      >
        <!-- Header -->
        <div class="p-4 border-b border-white/[0.06] flex items-center justify-between">
          <h3 class="font-semibold text-sm text-white">缓存管理</h3>
          <div class="text-xs text-[#8e8e93] font-mono tabular-nums">
            {{ metrics?.used_human ?? '0 B' }} / {{ metrics?.max_human ?? '0 B' }}
          </div>
        </div>

        <!-- Summary bar -->
        <div class="px-4 py-2.5 bg-black/30 border-b border-white/[0.04] flex items-center gap-3 text-[11px] text-[#8e8e93]">
          <span>共 {{ items.length }} 个</span>
          <span v-if="activeCount > 0" class="text-emerald-400">活跃 {{ activeCount }} 个</span>
          <span v-if="hdCount > 0" class="text-rose">高清 {{ hdCount }} 个</span>
          <span v-if="sdCount > 0" class="text-amber">标清 {{ sdCount }} 个</span>
        </div>

        <!-- List -->
        <div class="flex-1 overflow-y-auto p-2 space-y-1">
          <div v-if="enrichedItems.length === 0" class="text-center py-10 text-[#8e8e93] text-sm">
            暂无缓存
          </div>

          <div
            v-for="item in enrichedItems"
            :key="item.hash"
            class="p-3 rounded-xl bg-black/20 hover:bg-black/30 transition-colors"
          >
            <!-- 第一行：code + tags -->
            <div class="flex items-center gap-2 mb-2">
              <span v-if="item.number" class="text-[10px] text-[#8e8e93] font-mono">#{{ item.number }}</span>
              <p class="text-[13px] font-semibold text-white truncate flex-1 min-w-0">
                {{ item.displayCode }}
              </p>
              <span
                class="shrink-0 text-[10px] px-1.5 py-0.5 rounded font-medium"
                :class="qualityClass(item)"
              >
                {{ item.quality === 'HD' ? '高清' : '标清' }}
              </span>
              <span
                class="shrink-0 text-[10px] px-1.5 py-0.5 rounded font-medium"
                :class="stateClass(item)"
              >
                {{ stateLabel(item) }}
              </span>
            </div>

            <!-- 第二行：tier + peers + speed -->
            <div class="flex items-center gap-2 mb-2 text-[10px] text-[#8e8e93]">
              <span class="px-1.5 py-0.5 rounded bg-white/[0.05] text-white/50">{{ tierLabel(item) }}</span>
              <span v-if="item.peers > 0">{{ item.peers }} peers</span>
              <span v-if="item.download_rate > 0" class="text-emerald-400">↓ {{ formatSpeed(item.download_rate) }}</span>
              <span v-if="item.upload_rate > 0" class="text-sky-400">↑ {{ formatSpeed(item.upload_rate) }}</span>
              <span v-if="item.verified_pieces > 0 && item.state?.includes('checking')" class="text-amber">
                已校验 {{ item.verified_pieces }} pcs
              </span>
            </div>

            <!-- 进度条 -->
            <div class="flex items-center gap-2 mb-2">
              <div class="flex-1 h-1.5 rounded-full bg-white/5 overflow-hidden">
                <div
                  class="h-full rounded-full transition-all duration-500"
                  :class="progressBarClass(item)"
                  :style="{ width: `${Math.min(item.progress, 100)}%` }"
                />
              </div>
              <span class="text-[10px] text-[#8e8e93] font-mono tabular-nums w-12 text-right">{{ item.progress.toFixed(1) }}%</span>
            </div>

            <!-- 大小行 -->
            <div class="flex items-center justify-between text-[10px] text-[#8e8e93]/60 mb-2">
              <span>{{ formatSize(item.local_size) }} / {{ formatSize(item.video_size) }}</span>
              <span class="font-mono">{{ item.hash.slice(0, 12) }}...</span>
            </div>

            <!-- 操作按钮 -->
            <div class="flex gap-2">
              <button
                v-if="!item.head_ready && item.progress < 99.9"
                class="flex-1 text-[11px] bg-white/[0.06] hover:bg-white/[0.1] text-white py-1.5 rounded-lg transition-colors"
                @click="boostItem(item.hash)"
              >
                加速
              </button>
              <button
                class="text-[11px] text-[#ff453a] hover:text-[#ff6961] px-3 py-1.5 rounded-lg bg-[#ff453a]/10 hover:bg-[#ff453a]/15 transition-colors"
                @click="removeItem(item.hash)"
              >
                删除
              </button>
            </div>
          </div>
        </div>

        <!-- Footer -->
        <div class="p-3 border-t border-white/[0.06] flex gap-2 bg-black/20">
          <button
            class="flex-1 text-xs flex items-center justify-center gap-1.5 bg-white/[0.06] hover:bg-white/[0.1] text-white py-2.5 rounded-xl transition-colors font-medium"
            @click="refresh"
          >
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">
              <polyline points="23 4 23 10 17 10"/>
              <polyline points="1 20 1 14 7 14"/>
              <path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15"/>
            </svg>
            同步列表
          </button>
          <button
            class="flex-1 text-xs bg-[#ff453a]/10 hover:bg-[#ff453a]/15 text-[#ff453a] py-2.5 rounded-xl transition-colors font-medium"
            @click="clearAll"
          >
            清空全部
          </button>
        </div>
      </div>
    </Transition>
  </div>
</template>

<script setup lang="ts">
import type { CacheMetrics, Star } from '~/types/api'

const props = defineProps<{
  stars?: Star[]
}>()

const isOpen = ref(false)
const items = ref<any[]>([])
const metrics = ref<CacheMetrics | null>(null)

const { getCacheItems, getCacheMetrics, deleteCache, addTorrent } = useApi()

const WORK_CODE_RE = /[A-Z]{2,6}-\d{3,5}/i
function extractCode(name?: string): string | null {
  if (!name) return null
  const m = name.match(WORK_CODE_RE)
  return m ? m[0].toUpperCase() : null
}

const hashToInfo = computed(() => {
  const map: Record<string, { code: string; number: number }> = {}
  for (const star of (props.stars || [])) {
    for (const t of (star.titles || [])) {
      if (t.magnet) {
        const match = t.magnet.match(/xt=urn:btih:([a-f0-9]{40})/i)
        if (match) {
          const hash = match[1].toLowerCase()
          if (!map[hash]) {
            map[hash] = { code: t.code, number: t.number || 0 }
          }
        }
      }
    }
  }
  return map
})

const enrichedItems = computed(() => {
  return items.value.map(item => {
    const info = hashToInfo.value[item.hash]
    const code = item.work_code
      || info?.code
      || extractCode(item.video_file)
      || extractCode(item.name)
      || ''
    return {
      ...item,
      code,
      number: info?.number || 0,
      displayCode: code || item.name || item.hash.slice(0, 12),
    }
  })
})

const activeCount = computed(() => {
  return items.value.filter(i => i.progress > 0 && i.progress < 99.9).length
})

const hdCount = computed(() => items.value.filter(i => i.quality === 'HD').length)
const sdCount = computed(() => items.value.filter(i => i.quality === 'SD').length)

function formatSize(bytes: number): string {
  if (!bytes) return '0 B'
  if (bytes > 1024 * 1024 * 1024) return `${(bytes / 1024 / 1024 / 1024).toFixed(1)} GB`
  if (bytes > 1024 * 1024) return `${(bytes / 1024 / 1024).toFixed(0)} MB`
  if (bytes > 1024) return `${(bytes / 1024).toFixed(0)} KB`
  return `${bytes} B`
}

function formatSpeed(rate: number): string {
  if (rate > 1024 * 1024) return `${(rate / 1024 / 1024).toFixed(1)} MB/s`
  if (rate > 1024) return `${(rate / 1024).toFixed(1)} KB/s`
  return `${rate} B/s`
}

function stateClass(item: any): string {
  if (item.state === 'finished' || item.state === 'seeding') return 'bg-emerald-400/15 text-emerald-400'
  if (item.state === 'checking_files' || item.state === 'checking_resume_data') return 'bg-amber/15 text-amber'
  if (item.state === 'downloading') return 'bg-sky-400/15 text-sky-400'
  return 'bg-white/[0.06] text-[#8e8e93]'
}

function stateLabel(item: any): string {
  const map: Record<string, string> = {
    checking_files: '校验中',
    checking_resume_data: '校验中',
    downloading_metadata: '获取元数据',
    downloading: '下载中',
    finished: '已完成',
    seeding: '做种中',
    allocating: '分配中',
  }
  return map[item.state] || item.state || '等待中'
}

function qualityClass(item: any): string {
  return item.quality === 'HD'
    ? 'bg-rose/15 text-rose'
    : 'bg-white/[0.06] text-[#8e8e93]'
}

function tierLabel(item: any): string {
  const map: Record<string, string> = {
    hot: 'L1 热',
    warm: 'L2 温',
    seed: 'L3 冷',
    fragment: 'L4 碎',
  }
  return map[item.tier] || item.tier || '-'
}

function progressBarClass(item: any): string {
  if (item.progress >= 99.9) return 'bg-emerald-400'
  if (item.state?.includes('checking')) return 'bg-amber'
  return 'bg-rose'
}

async function refresh() {
  try {
    const cacheData = await getCacheItems() as any
    items.value = cacheData.items || []
    metrics.value = await getCacheMetrics() as CacheMetrics
  } catch (e) {
    console.error('refresh cache failed:', e)
  }
}

async function removeItem(hash: string) {
  try {
    await deleteCache(hash)
    await refresh()
  } catch (e) {
    console.error('delete cache failed:', e)
  }
}

async function boostItem(hash: string) {
  const item = items.value.find(i => i.hash === hash)
  if (!item?.magnet) return
  try {
    await addTorrent(item.magnet)
  } catch {
    // ignore
  }
}

async function clearAll() {
  if (!confirm('确定清空全部缓存？')) return
  for (const item of items.value) {
    try {
      await deleteCache(item.hash)
    } catch {
      // ignore
    }
  }
  await refresh()
}

watch(isOpen, (open) => {
  if (open) refresh()
})

let timer: ReturnType<typeof setInterval> | null = null
watch(isOpen, (open) => {
  if (open) {
    timer = setInterval(refresh, 5000)
  } else if (timer) {
    clearInterval(timer)
    timer = null
  }
})
</script>

<style scoped>
.slide-enter-active,
.slide-leave-active {
  transition: all 0.25s cubic-bezier(0.4, 0, 0.2, 1);
}
.slide-enter-from,
.slide-leave-to {
  opacity: 0;
  transform: translateY(8px) scale(0.96);
}
</style>
