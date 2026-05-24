<template>
  <div class="min-h-screen bg-black">
    <!-- Top bar -->
    <header class="fixed top-0 left-0 right-0 z-40 bg-black/90 backdrop-blur-xl">
      <div class="max-w-7xl mx-auto px-4 sm:px-6 h-12 flex items-center justify-between">
        <h1 class="text-[17px] font-semibold text-white tracking-tight">
          Star Archive
        </h1>
        <div class="flex items-center gap-4">
          <span
            class="w-2 h-2 rounded-full"
            :class="health?.status === 'ok' ? 'bg-[#30d158]' : 'bg-[#ff453a]'"
          />
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
      <StarNav :stars="displayStars" />
    </div>

    <!-- Main content -->
    <main class="pt-[100px] pb-24">
      <div class="max-w-7xl mx-auto px-4 sm:px-6 space-y-8">
        <!-- Add Star Panel -->
        <div class="p-4 rounded-2xl bg-[#1c1c1e] border border-white/[0.06]">
          <div class="flex items-center justify-between mb-3">
            <h2 class="text-[15px] font-semibold text-white">添加女优</h2>
            <span class="text-[12px] text-[#8e8e93]">ijavtorrent actress 页面</span>
          </div>
          <div class="flex items-center gap-3">
            <input
              v-model="newStarUrl"
              type="text"
              placeholder="https://ijavtorrent.com/actress/xxx-xxx-12345"
              class="flex-1 h-11 px-4 rounded-xl bg-black text-[14px] text-white placeholder:text-[#8e8e93]/50 outline-none border border-white/[0.06] focus:border-[#ff375f]/40 transition-colors"
              @keydown.enter="addStar"
            />
            <button
              class="h-11 px-5 rounded-xl bg-[#ff375f] text-white text-[14px] font-medium transition-all hover:brightness-110 active:scale-[0.97] disabled:opacity-40 disabled:cursor-not-allowed"
              :disabled="addingStar || !newStarUrl.trim()"
              @click="addStar"
            >
              <span v-if="addingStar" class="flex items-center gap-2">
                <svg class="animate-spin w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5">
                  <path d="M21 12a9 9 0 1 1-6.22-8.56" />
                </svg>
                添加中
              </span>
              <span v-else>添加</span>
            </button>
          </div>
          <p v-if="addError" class="mt-2.5 text-[13px] text-[#ff453a]">{{ addError }}</p>
          <p v-if="addSuccess" class="mt-2.5 text-[13px] text-[#30d158]">{{ addSuccess }}</p>
        </div>

        <!-- Recently Added Panel -->
        <div v-if="recentStars.length" class="p-4 rounded-2xl bg-[#1c1c1e] border border-white/[0.06]">
          <div class="flex items-center justify-between mb-3">
            <h2 class="text-[15px] font-semibold text-white">最近添加</h2>
            <button class="text-[12px] text-[#8e8e93] hover:text-white transition-colors" @click="clearRecent">
              清空
            </button>
          </div>
          <div class="flex flex-wrap gap-2">
            <div
              v-for="s in recentStars"
              :key="s.code"
              class="group flex items-center gap-2 px-3 py-1.5 rounded-full bg-[#2c2c2e] text-[13px] text-white hover:bg-[#3a3a3c] transition-colors cursor-pointer"
              @click="scrollToStar(s.code)"
            >
              <span class="w-1.5 h-1.5 rounded-full bg-[#ff375f]" />
              {{ s.name }}
              <span class="text-[#8e8e93]">{{ s.code }}</span>
            </div>
          </div>
        </div>

        <!-- Loading / Error -->
        <div v-if="pending" class="flex items-center justify-center py-40 gap-3 text-[#8e8e93]">
          <div class="w-5 h-5 rounded-full border-2 border-[#2c2c2e] border-t-white animate-spin" />
          <span class="text-[15px]">Loading...</span>
        </div>

        <div v-else-if="error" class="text-center py-40">
          <p class="text-[15px] text-[#ff453a]">Failed to load data</p>
        </div>

        <!-- Star Cards -->
        <div v-else class="space-y-14 md:space-y-20">
          <StarCard
            v-for="(star, index) in displayStars"
            :id="`star-${star.code}`"
            :key="star.code"
            :star="star"
            :index="index"
            @play="openVideo"
            @deleted="onStarDeleted"
          />
        </div>
      </div>
    </main>

    <VideoModal v-model:open="modalOpen" :hash="activeHash" />
    <CachePanel :stars="displayStars" />
  </div>
</template>

<script setup lang="ts">
import type { CacheMetrics } from '~/types/api'

interface HealthResponse {
  status: string
}

interface RecentStar {
  name: string
  code: string
  url: string
  addedAt: number
}

const config = useRuntimeConfig()
const { data: health } = useFetch<HealthResponse>('/api/health', { baseURL: config.public.apiBase })
const { stars, pending, error } = useStars()
const { preheat } = useCachePreheat()

const deletedCodes = ref<Set<string>>(new Set())
const displayStars = computed(() => stars.value?.filter(s => !deletedCodes.value.has(s.code)) ?? [])

const modalOpen = ref(false)
const activeHash = ref('')
const preheated = ref(false)

// Add Star
const newStarUrl = ref('')
const addingStar = ref(false)
const addError = ref('')
const addSuccess = ref('')
const recentStars = ref<RecentStar[]>([])

// Load recent from localStorage
onMounted(() => {
  try {
    const raw = localStorage.getItem('recentStars')
    if (raw) recentStars.value = JSON.parse(raw)
  } catch { /* ignore */ }
})

function saveRecent() {
  localStorage.setItem('recentStars', JSON.stringify(recentStars.value.slice(0, 20)))
}

function clearRecent() {
  recentStars.value = []
  localStorage.removeItem('recentStars')
}

function scrollToStar(code: string) {
  const el = document.getElementById(`star-${code}`)
  if (el) {
    el.scrollIntoView({ behavior: 'smooth', block: 'start' })
  }
}

function onStarDeleted(code: string) {
  deletedCodes.value.add(code)
  recentStars.value = recentStars.value.filter(s => s.code !== code)
  saveRecent()
}

async function addStar() {
  const url = newStarUrl.value.trim()
  if (!url) return
  addError.value = ''
  addSuccess.value = ''
  addingStar.value = true

  try {
    const res = await $fetch('/api/stars/add', {
      baseURL: config.public.apiBase,
      method: 'POST',
      body: { star_page_url: url },
    }) as any

    addSuccess.value = `已添加 ${res.name} (${res.code})，发现 ${res.titles_found} 部作品，后台同步中...`
    newStarUrl.value = ''

    // Push to recent
    recentStars.value.unshift({
      name: res.name,
      code: res.code,
      url: res.star_page_url,
      addedAt: Date.now(),
    })
    saveRecent()

    // Refresh stars list
    await refreshNuxtData('stars')
  } catch (e: any) {
    const msg = e?.data?.detail || e?.message || '添加失败'
    addError.value = msg
  } finally {
    addingStar.value = false
  }
}

function openVideo(magnet: string) {
  const match = magnet.match(/xt=urn:btih:([a-f0-9]{40})/i)
  if (match) {
    activeHash.value = match[1].toLowerCase()
    modalOpen.value = true
  }
}

// Sync state
const syncRunning = ref(false)
const syncElapsed = ref(0)
const syncError = ref('')
let syncTimer: ReturnType<typeof setInterval> | null = null
let syncStartTime = 0

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
