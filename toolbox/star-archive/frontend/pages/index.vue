<template>
  <div class="min-h-screen">
    <!-- Top navigation bar -->
    <header
      class="fixed top-0 left-0 right-0 z-40 glass-strong border-b border-glass-border"
    >
      <div class="max-w-7xl mx-auto px-6 h-16 flex items-center justify-between">
        <h1 class="text-xl font-display font-semibold tracking-wide text-gradient-rose">
          Star Archive
        </h1>
        <div class="flex items-center gap-5 text-xs text-foreground-muted">
          <span class="flex items-center gap-2">
            <span
              class="w-2 h-2 rounded-full animate-pulse-slow"
              :class="health?.status === 'ok' ? 'bg-rose shadow-rose-glow' : 'bg-rose-dark'"
            />
            {{ health?.status ?? '...' }}
          </span>
          <span v-if="metrics?.used_human" class="font-mono tabular-nums glass px-3 py-1 rounded-full">
            {{ metrics.used_human }}
          </span>
          <!-- Refresh / Sync button -->
          <button
            class="flex items-center gap-1.5 glass px-3 py-1.5 rounded-full text-xs transition-colors hover:bg-white/10 active:bg-white/5 disabled:opacity-50 disabled:cursor-not-allowed"
            :disabled="syncRunning"
            @click="startSync"
          >
            <span
              class="w-3 h-3 transition-transform"
              :class="{ 'animate-spin': syncRunning }"
            >
              ↻
            </span>
            <span v-if="syncRunning">同步中 {{ syncElapsed }}s</span>
            <span v-else>刷新作品</span>
          </button>
        </div>
      </div>
    </header>

    <!-- Star navigation pills -->
    <div class="fixed top-16 left-0 right-0 z-30 glass border-b border-glass-border">
      <StarNav :stars="stars ?? []" />
    </div>

    <!-- Main content -->
    <main class="pt-32 pb-20">
      <div class="max-w-7xl mx-auto px-6">
        <div v-if="pending" class="flex items-center justify-center py-32 gap-3 text-foreground-muted">
          <div class="w-5 h-5 rounded-full border-2 border-glass-border border-t-rose animate-spin" />
          <span class="text-sm font-light">Loading...</span>
        </div>

        <div v-else-if="error" class="text-center py-32">
          <p class="text-rose text-sm">Failed to load data</p>
        </div>

        <div v-else class="space-y-10 md:space-y-14">
          <StarCard
            v-for="(star, index) in stars"
            :key="star.code"
            :star="star"
            :index="index"
            @play="openVideo"
          />
        </div>
      </div>
    </main>

    <VideoModal v-model:open="modalOpen" :hash="activeHash" />
    <CachePanel :stars="stars ?? []" />
  </div>
</template>

<script setup lang="ts">
import type { CacheMetrics } from '~/types/api'

interface HealthResponse {
  status: string
}

const config = useRuntimeConfig()
const { data: health } = useFetch<HealthResponse>('/api/health', { baseURL: config.public.apiBase })
const { data: metrics } = useFetch<CacheMetrics>('/api/cache/metrics', { baseURL: config.public.apiBase })
const { stars, pending, error } = useStars()
const { preheat } = useCachePreheat()

const modalOpen = ref(false)
const activeHash = ref('')
const preheated = ref(false)

// Sync state
const syncRunning = ref(false)
const syncElapsed = ref(0)
const syncError = ref('')
let syncTimer: ReturnType<typeof setInterval> | null = null
let syncStartTime = 0

function openVideo(magnet: string) {
  const match = magnet.match(/xt=urn:btih:([a-f0-9]{40})/i)
  if (match) {
    activeHash.value = match[1].toLowerCase()
    modalOpen.value = true
  }
}

async function startSync() {
  if (syncRunning.value) return
  syncError.value = ''

  try {
    const res = await $fetch('/api/stars/sync', {
      baseURL: config.public.apiBase,
      method: 'POST',
    }) as any

    if (res.status === 'started') {
      syncRunning.value = true
      syncStartTime = Date.now()
      beginPolling()
    } else if (res.status === 'running') {
      syncRunning.value = true
      syncStartTime = Date.now() - (res.elapsed || 0) * 1000
      beginPolling()
    }
  } catch (e: any) {
    syncError.value = e?.message || '启动同步失败'
  }
}

function beginPolling() {
  if (syncTimer) clearInterval(syncTimer)
  syncTimer = setInterval(async () => {
    syncElapsed.value = Math.floor((Date.now() - syncStartTime) / 1000)

    try {
      const status = await $fetch('/api/stars/sync', {
        baseURL: config.public.apiBase,
      }) as any

      if (!status.running) {
        syncRunning.value = false
        if (syncTimer) {
          clearInterval(syncTimer)
          syncTimer = null
        }
        if (status.last_error) {
          syncError.value = status.last_error.slice(0, 200)
        }
        // Refresh stars data after sync completes
        await refreshNuxtData('stars')
      }
    } catch {
      // ignore polling errors
    }
  }, 2000)
}

// Stop polling on unmount
onUnmounted(() => {
  if (syncTimer) clearInterval(syncTimer)
})

// 页面加载完成后预热缓存（每个 star 第 1,4,7... 个作品）
watch(() => stars.value, (val) => {
  if (val && val.length > 0 && !preheated.value) {
    preheated.value = true
    preheat(val)
  }
}, { immediate: true })
</script>
