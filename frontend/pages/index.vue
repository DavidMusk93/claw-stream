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
            <template v-else>
              <span class="relative flex h-[13px] w-[13px]">
                <span class="animate-ping absolute inline-flex h-full w-full rounded-full bg-white opacity-75" />
                <span class="relative inline-flex rounded-full h-[13px] w-[13px] bg-white/90" />
              </span>
            </template>
            <span v-if="syncRunning" class="hidden sm:inline">Syncing...</span>
            <span v-else class="hidden sm:inline">Refresh</span>
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
            <h2 class="text-[15px] font-semibold text-white">Add Star</h2>
            <span class="text-[12px] text-[#8e8e93]">ijavtorrent actress page</span>
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
                Adding...
              </span>
              <span v-else>Add</span>
            </button>
          </div>
          <p v-if="addError" class="mt-2.5 text-[13px] text-[#ff453a]">{{ addError }}</p>
          <p v-if="addSuccess" class="mt-2.5 text-[13px] text-[#30d158]">{{ addSuccess }}</p>
        </div>

        <!-- Recently Added Panel -->
        <div v-if="recentStars.length" class="p-4 rounded-2xl bg-[#1c1c1e] border border-white/[0.06]">
          <div class="flex items-center justify-between mb-3">
            <h2 class="text-[15px] font-semibold text-white">Recently Added</h2>
            <button class="text-[12px] text-[#8e8e93] hover:text-white transition-colors" @click="clearRecent">
              Clear
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
            :id="`star-${star.code.toLowerCase()}`"
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

    <!-- Sync result toast -->
    <Transition
      enter-active-class="transition duration-300 ease-out"
      enter-from-class="translate-y-4 opacity-0"
      enter-to-class="translate-y-0 opacity-100"
      leave-active-class="transition duration-200 ease-in"
      leave-from-class="translate-y-0 opacity-100"
      leave-to-class="translate-y-4 opacity-0"
    >
      <div
        v-if="toastVisible"
        class="fixed top-16 left-1/2 -translate-x-1/2 z-50 flex items-start gap-3 px-4 py-3 rounded-2xl shadow-2xl max-w-sm w-[90vw]"
        :class="toastType === 'success'
          ? 'bg-[#1c1c1e] border border-white/[0.06]'
          : 'bg-[#1c1c1e] border border-[#ff453a]/30'"
        @click="dismissToast"
      >
        <span
          class="w-5 h-5 rounded-full flex items-center justify-center shrink-0 mt-0.5"
          :class="toastType === 'success' ? 'bg-[#30d158]/15 text-[#30d158]' : 'bg-[#ff453a]/15 text-[#ff453a]'"
        >
          <svg v-if="toastType === 'success'" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round">
            <polyline points="20 6 9 17 4 12" />
          </svg>
          <svg v-else width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">
            <circle cx="12" cy="12" r="10" />
            <line x1="12" y1="8" x2="12" y2="12" />
            <line x1="12" y1="16" x2="12.01" y2="16" />
          </svg>
        </span>
        <div class="flex-1 min-w-0">
          <p class="text-[13px] font-semibold text-white leading-snug">{{ toastMessage }}</p>
          <p v-if="toastDetail" class="text-[12px] text-[#8e8e93] mt-0.5 leading-snug">{{ toastDetail }}</p>
        </div>
        <button class="text-[#8e8e93]/60 hover:text-white transition-colors shrink-0 mt-0.5">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">
            <line x1="18" y1="6" x2="6" y2="18" />
            <line x1="6" y1="6" x2="18" y2="18" />
          </svg>
        </button>
      </div>
    </Transition>
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

// Poll for newly-added star titles
let addStarPollTimer: ReturnType<typeof setInterval> | null = null

function stopAddStarPoll() {
  if (addStarPollTimer) {
    clearInterval(addStarPollTimer)
    addStarPollTimer = null
  }
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

    const addedCode = res.code as string
    const addedName = res.name as string
    const titlesFound = res.titles_found as number

    addSuccess.value = `Added ${addedName} (${addedCode}), ${titlesFound} titles found, syncing in background...`
    newStarUrl.value = ''

    // Push to recent
    recentStars.value.unshift({
      name: addedName,
      code: addedCode,
      url: res.star_page_url,
      addedAt: Date.now(),
    })
    saveRecent()

    // Refresh stars list (may show 0 titles if bg sync hasn't finished)
    await refreshNuxtData('stars')
  } catch (e: any) {
    const msg = e?.data?.detail || e?.message || 'Failed to add'
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
const syncError = ref('')
let syncStartTime = 0

// Toast notification for sync completion
const toastVisible = ref(false)
const toastMessage = ref('')
const toastDetail = ref('')
const toastType = ref<'success' | 'error'>('success')
let toastTimer: ReturnType<typeof setTimeout> | null = null

function showToast(message: string, detail: string = '', type: 'success' | 'error' = 'success') {
  toastMessage.value = message
  toastDetail.value = detail
  toastType.value = type
  toastVisible.value = true
  if (toastTimer) clearTimeout(toastTimer)
  toastTimer = setTimeout(() => { toastVisible.value = false }, 4000)
}

function dismissToast() {
  toastVisible.value = false
  if (toastTimer) clearTimeout(toastTimer)
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
    } else if (res.status === 'running') {
      syncRunning.value = true
      syncStartTime = Date.now() - (res.elapsed || 0) * 1000
    }
  } catch (e: any) {
    syncError.value = e?.message || 'Failed to start sync'
  }
}

async function clearStarsServiceWorkerCache() {
  if (typeof window !== 'undefined' && 'caches' in window) {
    try {
      await caches.delete('stars-cache')
    } catch {
      // ignore
    }
  }
}

function handleSyncCompleted(data: any) {
  syncRunning.value = false
  const totalNew = data.total_new ?? 0
  const elapsed = data.elapsed ?? 0
  if (totalNew > 0) {
    showToast(
      `${totalNew} new title${totalNew > 1 ? 's' : ''} added`,
      `Synced in ${elapsed}s`,
      'success'
    )
  } else {
    showToast('All caught up', `Checked in ${elapsed}s — no new releases`, 'success')
  }
  clearStarsServiceWorkerCache()
  refreshNuxtData('stars')
}

function handleSyncError(data: any) {
  syncRunning.value = false
  syncError.value = data.error || 'Sync failed'
  showToast('Sync failed', data.error || '', 'error')
}

function handleStarReady(data: any) {
  addSuccess.value = `Added ${data.name} (${data.code}), ${data.titles_count} titles ready`
  refreshNuxtData('stars')
}

// Subscribe to SSE events on mount
onMounted(() => {
  const { onServerEvent } = useEventSource()
  const unsubs: (() => void)[] = []

  unsubs.push(onServerEvent('sync.started', () => {
    syncRunning.value = true
    syncStartTime = Date.now()
  }))

  unsubs.push(onServerEvent('sync.completed', handleSyncCompleted))
  unsubs.push(onServerEvent('sync.error', handleSyncError))
  unsubs.push(onServerEvent('star.ready', handleStarReady))

  onUnmounted(() => {
    unsubs.forEach((fn) => fn())
    if (toastTimer) clearTimeout(toastTimer)
  })
})

watch(() => stars.value, (val) => {
  if (val && val.length > 0 && !preheated.value) {
    preheated.value = true
    preheat(val)
  }
}, { immediate: true })
</script>
