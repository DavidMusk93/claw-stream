<template>
  <div class="min-h-screen bg-void">
    <!-- Top bar -->
    <header class="fixed top-0 left-0 right-0 z-40 bg-white/90 backdrop-blur-xl border-b border-black/[0.06]">
      <div class="max-w-7xl mx-auto px-4 sm:px-6 h-14 flex items-center justify-between">
        <div class="flex items-center gap-2.5">
          <img src="/logo.png" alt="Star Archive logo" class="w-8 h-8 -my-1 rounded-full" />
          <h1 class="text-[19px] font-semibold text-foreground tracking-tight">
            Star Archive
          </h1>
          <span
            class="w-2 h-2 rounded-full"
            :class="health?.status === 'ok' ? 'bg-[#30d158]' : 'bg-[#ff453a]'"
          />
        </div>
        <button
          class="flex items-center gap-2 h-10 px-4 rounded-full text-[14px] font-medium transition-all duration-200 active:scale-[0.97] disabled:cursor-default"
          :class="syncRunning
            ? 'bg-black/[0.04] text-foreground-muted'
            : 'bg-black text-white hover:bg-black/85 shadow-sm'"
          :disabled="syncRunning"
          @click="startSync"
        >
          <svg
            width="15"
            height="15"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            stroke-width="2.5"
            stroke-linecap="round"
            stroke-linejoin="round"
            :class="{ 'animate-spin': syncRunning }"
          >
            <polyline points="23 4 23 10 17 10" />
            <path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10" />
          </svg>
          <span v-if="syncRunning" class="tabular-nums">{{ syncProgressText || 'Syncing…' }}</span>
          <span v-else class="hidden sm:inline">Refresh</span>
        </button>
      </div>
    </header>

    <!-- Star navigation pills -->
    <div class="fixed top-14 left-0 right-0 z-30 bg-white/90 backdrop-blur-xl border-b border-black/[0.06]">
      <StarNav :stars="displayStars" />
    </div>

    <!-- Main content -->
    <main class="pt-[120px] pb-24">
      <div class="max-w-7xl mx-auto px-4 sm:px-6 space-y-12">
        <!-- Add Star Panel -->
        <div class="p-5 sm:p-6 rounded-2xl bg-white border border-black/[0.06] shadow-sm">
          <div class="flex items-center justify-between mb-4">
            <h2 class="text-[17px] font-semibold text-foreground tracking-tight">Add Star</h2>
            <span class="text-[13px] text-foreground-muted">ijavtorrent actress page</span>
          </div>
          <div class="flex items-center gap-3">
            <input
              v-model="newStarUrl"
              type="text"
              placeholder="https://ijavtorrent.com/actress/xxx-xxx-12345"
              class="flex-1 h-12 px-4 rounded-xl bg-[#F5F5F7] text-[15px] text-foreground placeholder:text-foreground-muted/50 outline-none border border-black/[0.06] focus:border-[#ff375f]/40 transition-colors"
              @keydown.enter="addStar"
            />
            <button
              class="h-12 px-6 rounded-xl bg-[#ff375f] text-white text-[15px] font-semibold transition-all hover:brightness-110 active:scale-[0.97] disabled:opacity-40 disabled:cursor-not-allowed"
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
          <p v-if="addError" class="mt-3 text-[14px] text-[#ff453a]">{{ addError }}</p>
          <p v-if="addSuccess" class="mt-3 text-[14px] text-[#30d158]">{{ addSuccess }}</p>
        </div>

        <!-- Recently Added Panel -->
        <div v-if="recentStars.length" class="p-5 sm:p-6 rounded-2xl bg-white border border-black/[0.06] shadow-sm">
          <div class="flex items-center justify-between mb-4">
            <h2 class="text-[17px] font-semibold text-foreground tracking-tight">Recently Added</h2>
            <button class="text-[13px] text-foreground-muted hover:text-foreground transition-colors" @click="clearRecent">
              Clear
            </button>
          </div>
          <div class="flex flex-wrap gap-2.5">
            <div
              v-for="s in recentStars"
              :key="s.code"
              class="group flex items-center gap-2 px-3.5 py-2 rounded-full bg-[#F2F2F7] text-[14px] text-foreground hover:bg-[#E5E5EA] transition-all cursor-pointer active:scale-[0.97]"
              @click="scrollToStar(s.code)"
            >
              <span class="w-1.5 h-1.5 rounded-full bg-[#ff375f]" />
              {{ s.name }}
              <span class="text-foreground-muted">{{ s.code }}</span>
            </div>
          </div>
        </div>

        <!-- Loading skeletons -->
        <div v-if="pending" class="space-y-16 md:space-y-24">
          <div
            v-for="n in 3"
            :key="n"
            class="space-y-3"
          >
            <Skeleton class="h-8 w-48 rounded-lg" />
            <div class="flex flex-col sm:flex-row gap-5">
              <Skeleton class="w-full sm:w-[280px] md:w-[340px] lg:w-[400px] aspect-[2/3] rounded-xl" />
              <div class="flex-1 space-y-3 py-4">
                <Skeleton class="h-8 w-3/4 rounded-lg" />
                <Skeleton class="h-4 w-full rounded" />
                <Skeleton class="h-4 w-5/6 rounded" />
                <Skeleton class="h-4 w-4/6 rounded" />
                <div class="flex gap-3 pt-3">
                  <Skeleton class="h-10 w-24 rounded-full" />
                  <Skeleton class="h-10 w-28 rounded-full" />
                  <Skeleton class="h-10 w-20 rounded-full" />
                </div>
              </div>
            </div>
            <div class="flex gap-3 overflow-hidden">
              <Skeleton v-for="i in 6" :key="i" class="shrink-0 w-[100px] sm:w-[120px] md:w-[140px] aspect-[2/3] rounded-lg" />
            </div>
          </div>
        </div>

        <!-- Error -->
        <div v-else-if="error" class="text-center py-40">
          <div class="inline-flex items-center justify-center w-16 h-16 rounded-full bg-[#ff453a]/10 text-[#ff453a] mb-5">
            <svg width="30" height="30" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
              <circle cx="12" cy="12" r="10"/>
              <line x1="12" y1="8" x2="12" y2="12"/>
              <line x1="12" y1="16" x2="12.01" y2="16"/>
            </svg>
          </div>
          <p class="text-[17px] text-[#ff453a]">Failed to load data</p>
          <button
            class="mt-5 px-5 py-2.5 rounded-full bg-black/[0.06] text-foreground text-[14px] font-medium hover:bg-black/[0.1] active:scale-[0.97] transition-all"
            @click="refreshNuxtData('stars')"
          >
            Retry
          </button>
        </div>

        <!-- Empty state -->
        <div v-else-if="displayStars.length === 0" class="text-center py-32">
          <div class="inline-flex items-center justify-center w-16 h-16 rounded-full bg-black/[0.04] text-foreground-muted mb-4">
            <svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">
              <path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"/>
              <circle cx="12" cy="7" r="4"/>
            </svg>
          </div>
          <p class="text-[15px] text-foreground-muted">No stars yet. Add one above.</p>
        </div>

        <!-- Star Cards -->
        <div v-else class="space-y-16 md:space-y-24">
          <div
            v-for="(star, index) in displayStars"
            :id="`star-${star.code.toLowerCase()}`"
            :key="star.code"
            ref="starRefs"
            :data-code="star.code"
          >
            <StarCard
              v-if="visibleCodes.has(star.code)"
              :star="star"
              :index="index"
              @play="openVideo"
              @deleted="onStarDeleted"
            />
            <div v-else class="space-y-3">
              <Skeleton class="h-8 w-40 rounded-lg" />
              <div class="flex flex-col sm:flex-row gap-5">
                <Skeleton class="w-full sm:w-[280px] md:w-[340px] lg:w-[400px] aspect-[2/3] rounded-xl" />
                <div class="flex-1 space-y-3 py-4">
                  <Skeleton class="h-8 w-3/4 rounded-lg" />
                  <Skeleton class="h-4 w-full rounded" />
                  <Skeleton class="h-4 w-5/6 rounded" />
                  <Skeleton class="h-4 w-4/6 rounded" />
                  <div class="flex gap-3 pt-3">
                    <Skeleton class="h-10 w-24 rounded-full" />
                    <Skeleton class="h-10 w-28 rounded-full" />
                    <Skeleton class="h-10 w-20 rounded-full" />
                  </div>
                </div>
              </div>
              <div class="flex gap-3 overflow-hidden">
                <Skeleton v-for="i in 6" :key="i" class="shrink-0 w-[100px] sm:w-[120px] md:w-[140px] aspect-[2/3] rounded-lg" />
              </div>
            </div>
          </div>
        </div>
      </div>
    </main>

    <VideoModal v-model:open="modalOpen" :hash="activeHash" />
    <CachePanel :stars="displayStars" />

    <!-- Sync progress toast -->
    <SyncToast
      :visible="toastVisible"
      :state="toastState"
      :title="toastTitle"
      :detail="toastDetail"
      :progress-text="toastProgressText"
      :fraction="toastFraction"
      @dismiss="dismissToast"
    />
  </div>
</template>

<script setup lang="ts">
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

const deletedCodes = ref<Set<string>>(new Set())
// Display filter: hide VR / multi-star (共演/omnibus) works, cap per-star list
const displayStars = computed(() =>
  (stars.value ?? [])
    .filter(s => !deletedCodes.value.has(s.code))
    .map(s => ({ ...s, titles: filterDisplayTitles(s, stars.value ?? []) }))
)

const visibleCodes = ref<Set<string>>(new Set())
const starRefs = ref<HTMLElement[]>([])

const modalOpen = ref(false)
const activeHash = ref('')

const newStarUrl = ref('')
const addingStar = ref(false)
const addError = ref('')
const addSuccess = ref('')
const recentStars = ref<RecentStar[]>([])

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
  const el = document.getElementById(`star-${code.toLowerCase()}`)
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

    const addedCode = res.code as string
    const addedName = res.name as string
    const titlesFound = res.titles_found as number

    addSuccess.value = `Added ${addedName} (${addedCode}), ${titlesFound} titles found, syncing in background...`
    newStarUrl.value = ''

    recentStars.value.unshift({
      name: addedName,
      code: addedCode,
      url: res.star_page_url,
      addedAt: Date.now(),
    })
    saveRecent()

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

const syncRunning = ref(false)
const syncError = ref('')
const { getSyncStatus } = useApi()

async function fetchSyncStatus(silent = false) {
  try {
    const status = await getSyncStatus()
    syncRunning.value = status.running
    if (!silent && status.last_error) {
      syncError.value = status.last_error
    }
  } catch (e: any) {
    if (!silent) syncError.value = e?.message || 'Failed to check sync status'
  }
}

const toastVisible = ref(false)
const toastState = ref<'running' | 'success' | 'error'>('running')
const toastTitle = ref('')
const toastDetail = ref('')
const toastProgressText = ref('')
const toastFraction = ref(0)
const syncProgressText = ref('')
let toastTimer: ReturnType<typeof setTimeout> | null = null

function showRunningToast(title: string, detail = '', progressText = '', fraction = 0.02) {
  if (toastTimer) { clearTimeout(toastTimer); toastTimer = null }
  toastState.value = 'running'
  toastTitle.value = title
  toastDetail.value = detail
  toastProgressText.value = progressText
  toastFraction.value = fraction
  toastVisible.value = true
}

function finishToast(title: string, detail: string, state: 'success' | 'error') {
  toastState.value = state
  toastTitle.value = title
  toastDetail.value = detail
  toastProgressText.value = ''
  toastFraction.value = 1
  toastVisible.value = true
  if (toastTimer) clearTimeout(toastTimer)
  toastTimer = setTimeout(() => { toastVisible.value = false }, state === 'error' ? 6000 : 4000)
}

function dismissToast() {
  toastVisible.value = false
  if (toastTimer) clearTimeout(toastTimer)
}

// Live sync progress pushed via SSE sync.progress events.
// Phases: prepare → fetch (per star) → covers → write (per star).
// Since fetch/covers/write are pipelined per star, events interleave —
// keep the bar monotonic so it never jumps backwards mid-run.
let syncPeakFraction = 0
function syncBar(fraction: number): number {
  syncPeakFraction = Math.max(syncPeakFraction, fraction)
  return syncPeakFraction
}
function handleSyncProgress(data: any) {
  switch (data.phase) {
    case 'prepare':
      showRunningToast('Syncing…', data.detail || 'Preparing')
      break
    case 'fetch': {
      const done = data.done ?? 0
      const total = data.total ?? 0
      const detail = data.star
        ? (data.ok ? `${data.star} — ${data.titles} titles` : `${data.star} — fetch failed`)
        : 'Fetching ijavtorrent + sukebei RSS'
      showRunningToast('Fetching stars', detail, `${done}/${total}`, syncBar(total ? (done / total) * 0.8 : 0.02))
      syncProgressText.value = total ? `${done}/${total}` : ''
      break
    }
    case 'covers':
      showRunningToast('Downloading covers', `${data.count ?? 0} new covers`, '', syncBar(0.85))
      break
    case 'write': {
      const done = data.done ?? 0
      const total = data.total ?? 0
      const detail = data.star ? `${data.star}: +${data.new ?? 0} new` : ''
      showRunningToast('Writing to database', detail, total ? `${done}/${total}` : '', syncBar(0.85 + (total ? (done / total) * 0.13 : 0)))
      break
    }
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

    if (res.status === 'started' || res.status === 'running') {
      syncRunning.value = true
      syncProgressText.value = ''
      syncPeakFraction = 0
      showRunningToast('Syncing…', 'Starting sync')
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
  syncProgressText.value = ''
  const totalNew = data.total_new ?? 0
  const elapsed = data.elapsed ?? 0
  const failed: string[] = data.failed ?? []
  if (failed.length > 0) {
    finishToast(
      `Sync incomplete — ${failed.length} star${failed.length > 1 ? 's' : ''} unreachable`,
      `${totalNew} new title${totalNew === 1 ? '' : 's'} synced in ${elapsed}s; failed: ${failed.join(', ')}`,
      'error'
    )
  } else if (totalNew > 0) {
    finishToast(
      `${totalNew} new title${totalNew > 1 ? 's' : ''} added`,
      `Synced in ${elapsed}s`,
      'success'
    )
  } else {
    finishToast('All caught up', `Checked in ${elapsed}s — no new releases`, 'success')
  }
  clearStarsServiceWorkerCache()
  refreshNuxtData('stars')
}

function handleSyncError(data: any) {
  syncRunning.value = false
  syncProgressText.value = ''
  syncError.value = data.error || 'Sync failed'
  finishToast('Sync failed', data.error || '', 'error')
}

function handleStarReady(data: any) {
  addSuccess.value = `Added ${data.name} (${data.code}), ${data.titles_count} titles ready`
  refreshNuxtData('stars')
}

// SSE subscriptions
onMounted(() => {
  const { onServerEvent } = useEventSource()
  const unsubs: (() => void)[] = []

  fetchSyncStatus(true)

  unsubs.push(onServerEvent('sync.started', () => {
    syncRunning.value = true
    syncProgressText.value = ''
    showRunningToast('Syncing…', 'Starting sync')
  }))

  unsubs.push(onServerEvent('sync.progress', handleSyncProgress))
  unsubs.push(onServerEvent('sync.completed', handleSyncCompleted))
  unsubs.push(onServerEvent('sync.error', handleSyncError))
  unsubs.push(onServerEvent('star.ready', handleStarReady))
  // Server coalesced our event queue (slow client) — refetch state once.
  unsubs.push(onServerEvent('sync.resync_required', () => {
    fetchSyncStatus(true)
    refreshNuxtData('stars')
  }))

  onUnmounted(() => {
    unsubs.forEach((fn) => fn())
    if (toastTimer) clearTimeout(toastTimer)
  })
})

// Virtual rendering with IntersectionObserver
let observer: IntersectionObserver | null = null

onMounted(() => {
  if (!import.meta.client) return

  observer = new IntersectionObserver((entries) => {
    entries.forEach((entry) => {
      const code = entry.target.getAttribute('data-code')
      if (code && entry.isIntersecting && !visibleCodes.value.has(code)) {
        visibleCodes.value.add(code)
      }
    })
  }, {
    rootMargin: '600px',
    threshold: 0,
  })

  nextTick(() => {
    starRefs.value.forEach((el) => observer?.observe(el))
  })
})

watch(() => displayStars.value, () => {
  nextTick(() => {
    starRefs.value.forEach((el) => observer?.observe(el))
  })
})

onUnmounted(() => {
  if (observer) observer.disconnect()
})

</script>
