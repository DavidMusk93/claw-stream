<template>
  <div class="min-h-screen bg-black">
    <!-- Top bar: Apple-style minimal. Pure black, no glass, no border. -->
    <header class="fixed top-0 left-0 right-0 z-40 bg-black/90 backdrop-blur-xl">
      <div class="max-w-7xl mx-auto px-4 sm:px-6 h-12 flex items-center justify-between">
        <h1 class="text-[17px] font-semibold text-white tracking-tight">
          Star Archive
        </h1>
        <div class="flex items-center gap-4">
          <!-- Minimal health dot -->
          <span
            class="w-2 h-2 rounded-full"
            :class="health?.status === 'ok' ? 'bg-[#30d158]' : 'bg-[#ff453a]'"
          />
          <!-- Sync button: Apple-style pill -->
          <button
            class="flex items-center gap-1.5 px-3 py-1 rounded-full bg-[#1c1c1e] text-[13px] text-white transition-colors duration-200 hover:bg-[#2c2c2e] active:bg-[#3a3a3c] disabled:opacity-40 disabled:cursor-not-allowed"
            :disabled="syncRunning"
            @click="startSync"
          >
            <svg
              v-if="!syncRunning"
              width="13"
              height="13"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              stroke-width="2.5"
              stroke-linecap="round"
              stroke-linejoin="round"
            >
              <polyline points="23 4 23 10 17 10" />
              <path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10" />
            </svg>
            <svg
              v-else
              width="13"
              height="13"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              stroke-width="2.5"
              stroke-linecap="round"
              stroke-linejoin="round"
              class="animate-spin"
            >
              <path d="M21 12a9 9 0 1 1-6.22-8.56" />
            </svg>
            <span v-if="syncRunning" class="hidden sm:inline">同步中 {{ syncElapsed }}s</span>
            <span v-else class="hidden sm:inline">刷新</span>
          </button>
        </div>
      </div>
    </header>

    <!-- Star navigation pills -->
    <div class="fixed top-12 left-0 right-0 z-30 bg-black/90 backdrop-blur-xl">
      <StarNav :stars="stars ?? []" />
    </div>

    <!-- Main content -->
    <main class="pt-[100px] pb-24">
      <div class="max-w-7xl mx-auto px-4 sm:px-6">
        <div v-if="pending" class="flex items-center justify-center py-40 gap-3 text-[#8e8e93]">
          <div class="w-5 h-5 rounded-full border-2 border-[#2c2c2e] border-t-white animate-spin" />
          <span class="text-[15px]">Loading...</span>
        </div>

        <div v-else-if="error" class="text-center py-40">
          <p class="text-[15px] text-[#ff453a]">Failed to load data</p>
        </div>

        <div v-else class="space-y-14 md:space-y-20">
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
        await refreshNuxtData('stars')
      }
    } catch {
      // ignore
    }
  }, 2000)
}

onUnmounted(() => {
  if (syncTimer) clearInterval(syncTimer)
})

watch(() => stars.value, (val) => {
  if (val && val.length > 0 && !preheated.value) {
    preheated.value = true
    preheat(val)
  }
}, { immediate: true })
</script>
