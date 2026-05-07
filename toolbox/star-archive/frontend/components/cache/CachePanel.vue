<template>
  <div class="fixed bottom-4 right-4 z-50">
    <!-- Toggle button -->
    <button
      class="w-12 h-12 rounded-full bg-ios-blue text-white flex items-center justify-center shadow-ios transition-all duration-200 active:scale-95 relative"
      @click="isOpen = !isOpen"
      title="缓存管理"
    >
      <svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
        <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/>
        <polyline points="17 8 12 3 7 8"/>
        <line x1="12" y1="3" x2="12" y2="15"/>
      </svg>
      <!-- 下载中指示点 -->
      <span v-if="activeCount > 0" class="absolute -top-0.5 -right-0.5 w-3 h-3 rounded-full bg-green-500 border-2 border-black" />
    </button>

    <!-- Panel -->
    <Transition name="slide">
      <div
        v-if="isOpen"
        class="absolute bottom-14 right-0 w-96 max-h-[70vh] bg-ios-bg-secondary border border-ios-separator/50 rounded-2xl shadow-ios overflow-hidden flex flex-col"
      >
        <div class="p-4 border-b border-ios-separator/30 flex items-center justify-between">
          <h3 class="font-semibold text-sm">缓存管理</h3>
          <div class="text-xs text-ios-text-secondary font-mono tabular-nums">
            {{ metrics?.used_human ?? '0 B' }} / {{ metrics?.max_human ?? '0 B' }}
          </div>
        </div>

        <div class="flex-1 overflow-y-auto p-2">
          <div v-if="enrichedItems.length === 0" class="text-center py-8 text-ios-text-secondary text-sm">
            暂无缓存
          </div>

          <div
            v-for="item in enrichedItems"
            :key="item.hash"
            class="p-3 rounded-xl hover:bg-ios-bg-tertiary transition-colors space-y-2"
          >
            <!-- 第一行：作品信息 -->
            <div class="flex items-center justify-between gap-2">
              <div class="flex-1 min-w-0">
                <p class="text-xs font-semibold truncate text-white">{{ item.code || item.name || item.hash.slice(0, 12) }}</p>
                <p class="text-[10px] text-neutral-500 font-mono tabular-nums">{{ item.hash.slice(0, 16) }}...</p>
              </div>
              <span
                class="shrink-0 text-[10px] px-2 py-0.5 rounded-full font-medium"
                :class="statusClass(item)"
              >
                {{ statusLabel(item) }}
              </span>
            </div>

            <!-- 进度条 -->
            <div class="flex items-center gap-2">
              <div class="flex-1 h-1.5 rounded-full bg-neutral-800 overflow-hidden">
                <div
                  class="h-full rounded-full transition-all duration-500"
                  :class="item.progress >= 99.9 ? 'bg-green-500' : 'bg-[#0A84FF]'"
                  :style="{ width: `${Math.min(item.progress, 100)}%` }"
                />
              </div>
              <span class="text-[10px] text-neutral-400 font-mono tabular-nums w-10 text-right">{{ item.progress.toFixed(1) }}%</span>
            </div>

            <!-- 详情行 -->
            <div class="flex items-center justify-between text-[10px] text-neutral-500">
              <span>{{ formatSize(item.local_size) }} / {{ formatSize(item.video_size) }}</span>
              <span v-if="item.download_rate > 0">{{ formatSpeed(item.download_rate) }}</span>
              <span v-if="item.peers > 0">{{ item.peers }} peers</span>
            </div>

            <!-- 操作按钮 -->
            <div class="flex gap-2 pt-1">
              <button
                v-if="!item.head_ready && item.progress < 99.9"
                class="flex-1 text-[10px] bg-[#0A84FF]/15 hover:bg-[#0A84FF]/25 text-[#0A84FF] py-1.5 rounded-lg transition-colors font-medium"
                @click="boostItem(item.hash)"
              >
                加速
              </button>
              <button
                class="text-[10px] text-ios-red hover:text-ios-red/80 px-3 py-1.5 rounded-lg bg-ios-red/10 hover:bg-ios-red/20 transition-colors"
                @click="removeItem(item.hash)"
              >
                删除
              </button>
            </div>
          </div>
        </div>

        <div class="p-3 border-t border-ios-separator/30 flex gap-2">
          <button
            class="flex-1 text-xs bg-ios-bg-tertiary hover:bg-ios-gray-4 text-ios-text-primary py-2.5 rounded-xl transition-colors font-medium"
            @click="refresh"
          >
            刷新
          </button>
          <button
            class="flex-1 text-xs bg-ios-red/15 hover:bg-ios-red/25 text-ios-red py-2.5 rounded-xl transition-colors font-medium"
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

// hash -> code 映射
const hashToCode = computed(() => {
  const map: Record<string, string> = {}
  for (const star of (props.stars || [])) {
    for (const t of (star.titles || [])) {
      if (t.magnet) {
        const match = t.magnet.match(/xt=urn:btih:([a-f0-9]{40})/i)
        if (match) {
          map[match[1].toLowerCase()] = t.code
        }
      }
    }
  }
  return map
})

const enrichedItems = computed(() => {
  return items.value.map(item => ({
    ...item,
    code: hashToCode.value[item.hash] || '',
  }))
})

const activeCount = computed(() => {
  return items.value.filter(i => i.progress > 0 && i.progress < 99.9).length
})

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

function statusClass(item: any): string {
  if (item.progress >= 99.9) return 'bg-green-500/15 text-green-400'
  if (item.head_ready) return 'bg-[#0A84FF]/15 text-[#0A84FF]'
  if (item.progress > 0) return 'bg-yellow-500/15 text-yellow-400'
  return 'bg-neutral-700/50 text-neutral-500'
}

function statusLabel(item: any): string {
  if (item.progress >= 99.9) return '完成'
  if (item.head_ready) return '可播放'
  if (item.progress > 0) return '下载中'
  return '等待中'
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
  // 重新 add 以触发 play priority
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

// Auto refresh every 5s when open
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
