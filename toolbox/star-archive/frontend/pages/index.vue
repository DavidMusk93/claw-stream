<template>
  <div class="min-h-screen">
    <!-- Top navigation bar -->
    <header
      class="fixed top-0 left-0 right-0 z-40 backdrop-blur-xl bg-ios-black/80 border-b border-ios-separator/50"
    >
      <div class="max-w-7xl mx-auto px-6 h-14 flex items-center justify-between">
        <h1 class="text-lg font-semibold tracking-tight">
          Star Archive
        </h1>
        <div class="flex items-center gap-4 text-xs text-ios-text-secondary">
          <span class="flex items-center gap-1.5">
            <span
              class="w-1.5 h-1.5 rounded-full"
              :class="health?.status === 'ok' ? 'bg-ios-green' : 'bg-ios-red'"
            />
            {{ health?.status ?? '...' }}
          </span>
          <span v-if="metrics?.used_human" class="font-mono tabular-nums">
            {{ metrics.used_human }}
          </span>
        </div>
      </div>
    </header>

    <!-- Star navigation pills -->
    <div class="fixed top-14 left-0 right-0 z-30 backdrop-blur-lg bg-ios-black/60 border-b border-ios-separator/30">
      <StarNav :stars="stars ?? []" />
    </div>

    <!-- Main content -->
    <main class="pt-28 pb-12">
      <div class="max-w-7xl mx-auto px-6">
        <div v-if="pending" class="flex items-center justify-center py-32 gap-3 text-ios-text-secondary">
          <div class="w-5 h-5 rounded-full border-2 border-ios-separator border-t-ios-blue animate-spin" />
          <span class="text-sm">Loading...</span>
        </div>

        <div v-else-if="error" class="text-center py-32">
          <p class="text-ios-red text-sm">Failed to load data</p>
        </div>

        <div v-else class="space-y-20">
          <StarCard
            v-for="star in stars"
            :key="star.code"
            :star="star"
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
