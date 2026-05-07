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

function openVideo(magnet: string) {
  const match = magnet.match(/xt=urn:btih:([a-f0-9]{40})/i)
  if (match) {
    activeHash.value = match[1].toLowerCase()
    modalOpen.value = true
  }
}

// 页面加载完成后预热缓存（每个 star 第 1,4,7... 个作品）
watch(() => stars.value, (val) => {
  if (val && val.length > 0 && !preheated.value) {
    preheated.value = true
    preheat(val)
  }
}, { immediate: true })
</script>
