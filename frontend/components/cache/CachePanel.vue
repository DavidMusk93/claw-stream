<template>
  <div class="fixed bottom-4 right-4 z-50">
    <!-- Toggle button -->
    <button
      class="w-14 h-14 rounded-full bg-white border border-black/[0.08] text-foreground flex items-center justify-center shadow-lg transition-all duration-200 active:scale-[0.97] relative hover:border-black/20 hover:shadow-xl"
      @click="isOpen = !isOpen"
      title="Cache Manager"
    >
      <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
        <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/>
        <polyline points="17 8 12 3 7 8"/>
        <line x1="12" y1="3" x2="12" y2="15"/>
      </svg>
      <span v-if="activeCount > 0" class="absolute -top-0.5 -right-0.5 w-3.5 h-3.5 rounded-full bg-[#30d158] border-2 border-white shadow-[0_0_8px_rgba(48,209,88,0.4)]" />
    </button>

    <!-- Panel -->
    <Transition name="slide">
      <div
        v-if="isOpen"
        class="absolute bottom-20 sm:bottom-16 right-0 w-[92vw] sm:w-[440px] max-h-[calc(100dvh-180px)] sm:max-h-[calc(100dvh-180px)] bg-white/95 backdrop-blur-xl border border-black/[0.06] rounded-2xl shadow-2xl overflow-hidden flex flex-col"
      >
        <!-- Header -->
        <div class="p-4 border-b border-black/[0.06] flex items-center justify-between">
          <div>
            <h3 class="font-semibold text-[15px] text-foreground tracking-tight">Cache Manager</h3>
            <div class="text-[12px] text-foreground-muted mt-0.5">
              {{ items.length }} items · {{ activeCount }} active
            </div>
          </div>
          <div class="flex items-center gap-2">
            <div
              v-if="refreshing"
              class="w-4 h-4 rounded-full border-2 border-black/[0.08] border-t-[#ff375f] animate-spin"
            />
            <div class="text-right">
              <div class="text-[13px] text-foreground-muted font-mono tabular-nums">
                {{ metrics?.used_human ?? '0 B' }} / {{ metrics?.max_human ?? '0 B' }}
              </div>
            </div>
          </div>
        </div>

        <!-- Capacity bar -->
        <div class="px-4 py-2.5 bg-[#F5F5F7] border-b border-black/[0.04]">
          <div class="h-1.5 bg-black/[0.06] rounded-full overflow-hidden">
            <div
              class="h-full rounded-full transition-all duration-500"
              :class="usedPct > 90 ? 'bg-[#ff453a]' : usedPct > 70 ? 'bg-[#ff9f0a]' : 'bg-[#30d158]'"
              :style="{ width: `${Math.min(usedPct, 100)}%` }"
            />
          </div>
          <div class="mt-1.5 flex justify-between text-[11px] text-foreground-muted/70">
            <span>{{ usedPct.toFixed(1) }}% used</span>
            <span>{{ metrics?.torrent_count ?? 0 }} torrents</span>
          </div>
        </div>

        <!-- Lane legend -->
        <div class="px-4 py-2.5 bg-[#F2F2F7] border-b border-black/[0.04] flex items-center gap-3 text-[11px] text-foreground-muted/70 flex-wrap">
          <span class="flex items-center gap-1"><span class="w-2 h-2 rounded-sm bg-[#10b981]" />Cached</span>
          <span class="flex items-center gap-1"><span class="w-2 h-2 rounded-sm bg-[#f59e0b]" />Downloading</span>
          <span class="flex items-center gap-1"><span class="w-2 h-2 rounded-sm bg-[#ef4444]" />Corrupt</span>
          <span class="flex items-center gap-1"><span class="w-2 h-2 rounded-sm bg-[#1f2937]" />Missing</span>
        </div>

        <!-- List -->
        <div class="flex-1 overflow-y-auto p-2 space-y-1 min-h-0">
          <div v-if="loading" class="p-4 space-y-3">
            <Skeleton class="h-28 w-full rounded-xl" />
            <Skeleton class="h-28 w-full rounded-xl" />
            <Skeleton class="h-28 w-full rounded-xl" />
          </div>

          <div v-else-if="enrichedItems.length === 0" class="text-center py-12 text-foreground-muted text-sm">
            No cache items
          </div>

          <div
            v-for="item in enrichedItems"
            v-else
            :key="item.hash"
            class="p-3 rounded-xl bg-[#F2F2F7] hover:bg-[#F5F5F7] transition-colors"
          >
            <!-- Row 1: code + tags -->
            <div class="flex items-center gap-2 mb-2">
              <span v-if="item.number" class="text-[11px] text-foreground-muted font-mono">#{{ item.number }}</span>
              <p class="text-[14px] font-semibold text-foreground truncate flex-1 min-w-0">
                {{ item.displayCode }}
              </p>
              <span
                class="shrink-0 text-[11px] px-2 py-0.5 rounded-full font-medium"
                :class="qualityClass(item)"
              >
                {{ item.quality === 'HD' ? 'HD' : 'SD' }}
              </span>
              <span
                class="shrink-0 text-[11px] px-2 py-0.5 rounded-full font-medium"
                :class="stateClass(item)"
              >
                {{ stateLabel(item) }}
              </span>
            </div>

            <!-- Row 2: tier + peers + speed -->
            <div class="flex items-center gap-2 mb-2 text-[11px] text-foreground-muted flex-wrap">
              <span class="px-1.5 py-0.5 rounded-md bg-black/[0.05] text-foreground/50">{{ tierLabel(item) }}</span>
              <span v-if="item.peers > 0">{{ item.peers }} peers</span>
              <span v-if="item.download_rate > 0" class="text-[#30d158]">↓ {{ formatSpeed(item.download_rate) }}</span>
              <span v-if="item.upload_rate > 0" class="text-[#0a84ff]">↑ {{ formatSpeed(item.upload_rate) }}</span>
              <span v-if="item.verified_pieces > 0 && item.state?.includes('checking')" class="text-[#ff9f0a]">
                Verified {{ item.verified_pieces }} pcs
              </span>
            </div>

            <!-- Lane -->
            <div class="mb-2">
              <div class="flex h-2.5 rounded-md overflow-hidden">
                <div
                  v-for="(seg, idx) in item.piece_segments"
                  :key="idx"
                  class="h-full"
                  :style="{ width: `${100 / item.piece_segments.length}%`, backgroundColor: laneColor(seg[2]) }"
                  :title="`piece ${seg[0].toFixed(0)}%-${seg[1].toFixed(0)}%: ${laneLabel(seg[2])}`"
                />
              </div>
              <div class="mt-1 flex justify-between text-[11px] text-foreground-muted/50">
                <span>0%</span>
                <span>piece map ({{ item.piece_segments.length }} segments)</span>
                <span>100%</span>
              </div>
            </div>

            <!-- Size row -->
            <div class="flex items-center justify-between text-[11px] text-foreground-muted/60 mb-2">
              <span>{{ formatSize(item.local_size) }} / {{ formatSize(item.video_size) }}</span>
              <span class="font-mono">{{ item.hash.slice(0, 12) }}…</span>
            </div>

            <!-- Actions -->
            <div class="flex gap-2">
              <button
                v-if="!item.head_ready && item.progress < 99.9"
                class="flex-1 text-[12px] font-medium bg-black/[0.06] hover:bg-black/[0.1] text-foreground py-2 rounded-lg transition-all active:scale-[0.97]"
                @click="boostItem(item.hash)"
              >
                Boost
              </button>
              <button
                class="text-[12px] font-medium text-[#ff453a] hover:text-[#ff6961] px-3 py-2 rounded-lg bg-[#ff453a]/10 hover:bg-[#ff453a]/15 transition-all active:scale-[0.97]"
                @click="removeItem(item.hash)"
              >
                Remove
              </button>
            </div>
          </div>
        </div>

        <!-- Footer -->
        <div class="p-3 border-t border-black/[0.06] flex gap-2 bg-[#F2F2F7]">
          <button
            class="flex-1 text-[14px] flex items-center justify-center gap-1.5 bg-black/[0.06] hover:bg-black/[0.1] text-foreground py-3 rounded-xl transition-all font-medium active:scale-[0.97]"
            @click="refresh(true)"
          >
            <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">
              <polyline points="23 4 23 10 17 10"/>
              <polyline points="1 20 1 14 7 14"/>
              <path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15"/>
            </svg>
            Refresh
          </button>
          <button
            class="flex-1 text-[14px] bg-[#ff453a]/10 hover:bg-[#ff453a]/15 text-[#ff453a] py-3 rounded-xl transition-all font-medium active:scale-[0.97]"
            @click="clearAll"
          >
            Clear All
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
const loading = ref(false)
const refreshing = ref(false)
let timer: ReturnType<typeof setInterval> | null = null
let unsubCache: (() => void) | null = null
let refreshDebounceTimer: ReturnType<typeof setTimeout> | null = null

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
  const list = items.value.map(item => {
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
  return list.sort((a, b) => {
    if (a.number && b.number) return a.number - b.number
    if (a.number) return -1
    if (b.number) return 1
    return 0
  })
})

const activeCount = computed(() => items.value.filter(i => i.progress > 0 && i.progress < 99.9).length)

const usedPct = computed(() => {
  const used = metrics.value?.used_bytes || 0
  const max = metrics.value?.max_bytes || 0
  return max > 0 ? (used / max) * 100 : 0
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

function stateClass(item: any): string {
  if (item.state === 'finished' || item.state === 'seeding') return 'bg-[#30d158]/15 text-[#30d158]'
  if (item.state?.includes('checking')) return 'bg-[#ff9f0a]/15 text-[#ff9f0a]'
  if (item.state === 'downloading') return 'bg-[#0a84ff]/15 text-[#0a84ff]'
  return 'bg-black/[0.05] text-foreground-muted'
}

function stateLabel(item: any): string {
  const map: Record<string, string> = {
    checking_files: 'Verifying',
    checking_resume_data: 'Verifying',
    downloading_metadata: 'Metadata',
    downloading: 'Downloading',
    finished: 'Finished',
    seeding: 'Seeding',
    allocating: 'Allocating',
  }
  return map[item.state] || item.state || 'Waiting'
}

function qualityClass(item: any): string {
  return item.quality === 'HD'
    ? 'bg-[#ff375f]/15 text-[#ff375f]'
    : 'bg-black/[0.05] text-foreground-muted'
}

function tierLabel(item: any): string {
  const map: Record<string, string> = {
    hot: 'L1 Hot',
    warm: 'L2 Warm',
    seed: 'L3 Cold',
    fragment: 'L4 Fragment',
  }
  return map[item.tier] || item.tier || '-'
}

function laneColor(state: number): string {
  const colors: Record<number, string> = {
    0: '#1f2937',
    1: '#f59e0b',
    2: '#10b981',
    3: '#ef4444',
  }
  return colors[state] || '#1f2937'
}

function laneLabel(state: number): string {
  const labels: Record<number, string> = {
    0: 'Missing',
    1: 'Downloading',
    2: 'Cached',
    3: 'Corrupt',
  }
  return labels[state] || 'Unknown'
}

async function refresh(showLoading = false) {
  if (refreshing.value) return
  if (showLoading && items.value.length === 0) loading.value = true
  refreshing.value = true
  try {
    const cacheData = await getCacheItems() as any
    items.value = cacheData.items || []
    metrics.value = await getCacheMetrics() as CacheMetrics
  } catch (e) {
    console.error('refresh cache failed:', e)
  } finally {
    loading.value = false
    refreshing.value = false
  }
}

function scheduleRefresh() {
  if (refreshDebounceTimer) clearTimeout(refreshDebounceTimer)
  refreshDebounceTimer = setTimeout(() => refresh(false), 400)
}

async function removeItem(hash: string) {
  try {
    await deleteCache(hash)
    await refresh(false)
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
  if (!confirm('Clear all cache items?')) return
  loading.value = true
  try {
    await Promise.all(items.value.map(async (item) => {
      try {
        await deleteCache(item.hash)
      } catch {
        // ignore
      }
    }))
  } finally {
    loading.value = false
    await refresh(false)
  }
}

watch(isOpen, (open) => {
  if (open) {
    refresh(true)
    const { onServerEvent } = useEventSource()
    unsubCache = onServerEvent('cache.update', () => {
      scheduleRefresh()
    })
    timer = setInterval(() => refresh(false), 30000)
  } else {
    if (timer) {
      clearInterval(timer)
      timer = null
    }
    if (unsubCache) {
      unsubCache()
      unsubCache = null
    }
    if (refreshDebounceTimer) {
      clearTimeout(refreshDebounceTimer)
      refreshDebounceTimer = null
    }
  }
})

onUnmounted(() => {
  if (timer) clearInterval(timer)
  if (unsubCache) unsubCache()
  if (refreshDebounceTimer) clearTimeout(refreshDebounceTimer)
})
</script>

<style scoped>
.slide-enter-active,
.slide-leave-active {
  transition: all 0.3s cubic-bezier(0.16, 1, 0.3, 1);
}
.slide-enter-from,
.slide-leave-to {
  opacity: 0;
  transform: translateY(8px) scale(0.97);
}
</style>
