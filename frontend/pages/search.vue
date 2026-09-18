<template>
  <div class="min-h-screen bg-void">
    <!-- Top bar -->
    <header class="fixed top-0 left-0 right-0 z-40 bg-white/90 backdrop-blur-xl border-b border-black/[0.06]">
      <div class="max-w-7xl mx-auto px-4 sm:px-6 h-14 flex items-center gap-3">
        <NuxtLink
          to="/"
          class="flex items-center justify-center w-9 h-9 rounded-full bg-black/[0.04] text-foreground hover:bg-black/[0.08] transition-all active:scale-[0.97]"
          aria-label="Back to home"
        >
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">
            <polyline points="15 18 9 12 15 6" />
          </svg>
        </NuxtLink>
        <h1 class="text-[19px] font-semibold text-foreground tracking-tight">Search</h1>
        <span class="text-[13px] text-foreground-muted">ijavtorrent</span>
      </div>
    </header>

    <main class="pt-[72px] pb-24">
      <div class="max-w-7xl mx-auto px-4 sm:px-6 space-y-8">
        <!-- Search panel -->
        <div class="p-5 sm:p-6 rounded-2xl bg-white border border-black/[0.06] shadow-sm">
          <div class="flex items-center gap-3">
            <input
              v-model="query"
              type="text"
              placeholder="番号或关键词，如 SONE-560"
              class="flex-1 h-12 px-4 rounded-xl bg-[#F5F5F7] text-[15px] text-foreground placeholder:text-foreground-muted/50 outline-none border border-black/[0.06] focus:border-[#ff375f]/40 transition-colors"
              @keydown.enter="search"
            />
            <button
              class="h-12 px-6 rounded-xl bg-[#ff375f] text-white text-[15px] font-semibold transition-all hover:brightness-110 active:scale-[0.97] disabled:opacity-40 disabled:cursor-not-allowed"
              :disabled="loading || query.trim().length < 2"
              @click="search"
            >
              <span v-if="loading" class="flex items-center gap-2">
                <svg class="animate-spin w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5">
                  <path d="M21 12a9 9 0 1 1-6.22-8.56" />
                </svg>
                Searching...
              </span>
              <span v-else>Search</span>
            </button>
          </div>
          <p v-if="errorMsg" class="mt-3 text-[14px] text-[#ff453a]">{{ errorMsg }}</p>
        </div>

        <!-- Loading skeletons -->
        <div v-if="loading" class="grid sm:grid-cols-2 lg:grid-cols-3 gap-5">
          <div v-for="n in 6" :key="n" class="p-4 rounded-2xl bg-white border border-black/[0.06] shadow-sm space-y-3">
            <Skeleton class="w-full aspect-[3/2] rounded-xl" />
            <Skeleton class="h-5 w-24 rounded" />
            <Skeleton class="h-4 w-full rounded" />
            <Skeleton class="h-9 w-28 rounded-full" />
          </div>
        </div>

        <!-- Empty state -->
        <div v-else-if="searched && items.length === 0" class="text-center py-32">
          <div class="inline-flex items-center justify-center w-16 h-16 rounded-full bg-black/[0.04] text-foreground-muted mb-4">
            <svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">
              <circle cx="11" cy="11" r="8" />
              <line x1="21" y1="21" x2="16.65" y2="16.65" />
            </svg>
          </div>
          <p class="text-[15px] text-foreground-muted">No results for "{{ lastQuery }}"</p>
        </div>

        <!-- Results -->
        <div v-else-if="items.length" class="space-y-4">
          <p class="text-[13px] text-foreground-muted">{{ items.length }} results for "{{ lastQuery }}"</p>
          <div class="grid sm:grid-cols-2 lg:grid-cols-3 gap-5">
            <div
              v-for="item in items"
              :key="item.code"
              class="p-4 rounded-2xl bg-white border border-black/[0.06] shadow-sm space-y-3"
            >
              <!-- Cover -->
              <div class="relative w-full aspect-[3/2] rounded-xl overflow-hidden bg-[#F5F5F7]">
                <img
                  v-if="item.cover_url && !failedCovers.has(item.code)"
                  :src="item.cover_url"
                  :alt="item.code"
                  referrerpolicy="no-referrer"
                  loading="lazy"
                  class="w-full h-full object-cover"
                  @error="failedCovers.add(item.code)"
                />
                <div v-else class="w-full h-full flex items-center justify-center">
                  <span class="text-[15px] font-semibold text-foreground-muted/60 tracking-wide">{{ item.code }}</span>
                </div>
                <span
                  v-if="item.in_library"
                  class="absolute top-2 left-2 px-2.5 py-1 rounded-full bg-[#30d158] text-white text-[11px] font-semibold shadow-sm"
                >
                  已在库
                </span>
              </div>

              <!-- Meta -->
              <div>
                <div class="flex items-center gap-2 flex-wrap">
                  <span class="text-[15px] font-semibold text-foreground tracking-tight">{{ item.code }}</span>
                  <span v-if="item.resolution" class="px-2 py-0.5 rounded-full bg-[#F2F2F7] text-[11px] font-medium text-foreground-muted">{{ item.resolution }}</span>
                  <span v-if="item.size" class="px-2 py-0.5 rounded-full bg-[#F2F2F7] text-[11px] font-medium text-foreground-muted">{{ item.size }}</span>
                </div>
                <p v-if="item.title" class="mt-1.5 text-[13px] text-foreground-muted leading-snug line-clamp-2">{{ item.title }}</p>
                <p class="mt-1 text-[12px] text-foreground-muted/70">
                  <span v-if="item.release_date">{{ item.release_date }}</span>
                  <span v-if="item.views != null"> · {{ formatCount(item.views) }} views</span>
                </p>
              </div>

              <!-- Follow actions -->
              <div v-if="item.stars.length" class="flex flex-wrap gap-2 pt-1">
                <button
                  v-for="star in item.stars"
                  :key="star.url"
                  class="flex items-center gap-1.5 h-9 px-4 rounded-full text-[13px] font-medium transition-all active:scale-[0.97] disabled:cursor-default"
                  :class="isFollowed(star.url)
                    ? 'bg-[#30d158]/10 text-[#30d158]'
                    : 'bg-[#ff375f] text-white hover:brightness-110'"
                  :disabled="isFollowed(star.url) || followingUrls.has(star.url)"
                  @click="follow(item, star)"
                >
                  <svg v-if="isFollowed(star.url)" width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round">
                    <polyline points="20 6 9 17 4 12" />
                  </svg>
                  <svg v-else-if="followingUrls.has(star.url)" class="animate-spin w-3.5 h-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5">
                    <path d="M21 12a9 9 0 1 1-6.22-8.56" />
                  </svg>
                  <svg v-else width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">
                    <line x1="12" y1="5" x2="12" y2="19" />
                    <line x1="5" y1="12" x2="19" y2="12" />
                  </svg>
                  {{ isFollowed(star.url) ? `${star.name} · Following` : `Follow ${star.name}` }}
                </button>
              </div>
            </div>
          </div>
        </div>
      </div>
    </main>

    <!-- Follow feedback toast -->
    <SyncToast
      :visible="toastVisible"
      :state="toastState"
      :title="toastTitle"
      :detail="toastDetail"
      :fraction="1"
      @dismiss="toastVisible = false"
    />
  </div>
</template>

<script setup lang="ts">
import type { SearchResponse, SearchResultItem, SearchResultStar } from '~/types/api'

const config = useRuntimeConfig()
const { track } = useTrack()
const { add: addLog } = useEventLog()

const query = ref('')
const lastQuery = ref('')
const items = ref<SearchResultItem[]>([])
const loading = ref(false)
const searched = ref(false)
const errorMsg = ref('')

// Local follow state: server-reported `followed` plus optimistic updates,
// so a follow reflects on every card of the same actress immediately.
const followedUrls = ref<Set<string>>(new Set())
const followingUrls = ref<Set<string>>(new Set())
const failedCovers = ref<Set<string>>(new Set())

const toastVisible = ref(false)
const toastState = ref<'running' | 'success' | 'error'>('success')
const toastTitle = ref('')
const toastDetail = ref('')
let toastTimer: ReturnType<typeof setTimeout> | null = null

function showToast(title: string, detail: string, state: 'success' | 'error') {
  toastState.value = state
  toastTitle.value = title
  toastDetail.value = detail
  toastVisible.value = true
  if (toastTimer) clearTimeout(toastTimer)
  toastTimer = setTimeout(() => { toastVisible.value = false }, state === 'error' ? 6000 : 4000)
}

function isFollowed(url: string) {
  return followedUrls.value.has(url)
}

function formatCount(n: number) {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}k`
  return String(n)
}

async function search() {
  const q = query.value.trim()
  if (q.length < 2 || loading.value) return
  loading.value = true
  errorMsg.value = ''
  failedCovers.value = new Set()

  try {
    const res = await $fetch<SearchResponse>('/api/search', {
      baseURL: config.public.apiBase,
      params: { q },
    })
    items.value = res.items
    lastQuery.value = q
    searched.value = true
    followedUrls.value = new Set(
      res.items.flatMap(it => it.stars).filter(s => s.followed).map(s => s.url)
    )
    track('search', { meta: { query: q, count: res.count } })
  } catch (e: any) {
    errorMsg.value = e?.data?.detail || e?.message || 'Search failed'
    addLog({ kind: 'action', title: 'Search failed', detail: errorMsg.value, state: 'error' })
  } finally {
    loading.value = false
  }
}

async function follow(item: SearchResultItem, star: SearchResultStar) {
  if (isFollowed(star.url) || followingUrls.value.has(star.url)) return
  followingUrls.value = new Set([...followingUrls.value, star.url])

  try {
    const res = await $fetch('/api/stars/add', {
      baseURL: config.public.apiBase,
      method: 'POST',
      body: { star_page_url: star.url },
    }) as any

    followedUrls.value = new Set([...followedUrls.value, star.url])
    showToast(`Following ${res.name}`, `${res.titles_found} titles found, syncing in background`, 'success')
    track('add_star', { code: res.code, meta: { name: res.name, source: 'search', via: item.code } })
    addLog({ kind: 'action', title: `Followed ${res.name}`, detail: `via search ${item.code}`, state: 'success' })
    refreshNuxtData('stars')
  } catch (e: any) {
    if (e?.status === 409 || e?.response?.status === 409) {
      // Already followed — treat as success state
      followedUrls.value = new Set([...followedUrls.value, star.url])
    } else {
      const msg = e?.data?.detail || e?.message || 'Follow failed'
      showToast('Follow failed', msg, 'error')
      addLog({ kind: 'action', title: 'Follow failed', detail: msg, state: 'error' })
    }
  } finally {
    const next = new Set(followingUrls.value)
    next.delete(star.url)
    followingUrls.value = next
  }
}

onUnmounted(() => {
  if (toastTimer) clearTimeout(toastTimer)
})
</script>
