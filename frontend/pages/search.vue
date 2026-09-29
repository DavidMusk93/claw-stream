<template>
  <div class="min-h-screen bg-void">
    <!-- Top bar -->
    <header class="fixed top-0 left-0 right-0 z-40 bg-white/95 sm:bg-white/90 sm:backdrop-blur-xl border-b border-black/[0.06]">
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
        <span class="text-[13px] text-foreground-muted">ijavtorrent · sukebei</span>
      </div>
    </header>

    <main class="pt-[72px] pb-24">
      <div class="max-w-7xl mx-auto px-4 sm:px-6 space-y-8">
        <!-- Search panel -->
        <div class="p-5 sm:p-6 rounded-2xl bg-white border border-black/[0.06] shadow-sm">
          <div class="flex items-center gap-3">
            <div class="relative flex-1">
              <svg class="absolute left-4 top-1/2 -translate-y-1/2 text-foreground-muted/60" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                <circle cx="11" cy="11" r="8" />
                <line x1="21" y1="21" x2="16.65" y2="16.65" />
              </svg>
              <input
                v-model="query"
                type="text"
                placeholder="番号或关键词，如 SONE-560"
                class="w-full h-12 pl-11 pr-4 rounded-full bg-[#F5F5F7] text-[15px] text-foreground placeholder:text-foreground-muted/50 outline-none border border-transparent focus:border-[#ff375f]/40 focus:bg-white transition-colors"
                @keydown.enter="search"
              />
            </div>
            <button
              class="h-12 px-6 rounded-full bg-[#ff375f] text-white text-[15px] font-semibold transition-all hover:brightness-110 active:scale-[0.97] disabled:opacity-40 disabled:cursor-not-allowed shrink-0"
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
        <div v-if="loading" class="space-y-6">
          <div
            v-for="n in 3"
            :key="n"
            class="flex flex-col sm:flex-row gap-6 sm:gap-8 p-5 sm:p-7 rounded-3xl bg-white border border-black/[0.06] shadow-sm"
          >
            <Skeleton class="w-full sm:w-[300px] md:w-[340px] shrink-0 aspect-[3/2] rounded-2xl" />
            <div class="flex-1 space-y-3 py-1">
              <Skeleton class="h-9 w-40 rounded-lg" />
              <Skeleton class="h-4 w-full rounded" />
              <Skeleton class="h-4 w-2/3 rounded" />
              <Skeleton class="h-10 w-64 rounded-full" />
            </div>
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

        <!-- Results: hero cards in the home page's StarCard language -->
        <div v-else-if="items.length" class="space-y-6">
          <p class="text-[13px] text-foreground-muted px-1">{{ items.length }} results for "{{ lastQuery }}"</p>

          <div
            v-for="item in items"
            :key="item.code"
            class="flex flex-col sm:flex-row gap-6 sm:gap-8 p-5 sm:p-7 rounded-3xl bg-white border border-black/[0.06] shadow-sm"
          >
            <!-- Cover: natural aspect ratio, never cropped -->
            <div class="relative w-full sm:w-[300px] md:w-[340px] shrink-0 rounded-2xl overflow-hidden bg-black shadow-md">
              <img
                v-if="item.cover_url && !failedCovers.has(item.code)"
                :src="item.cover_url"
                :alt="item.code"
                referrerpolicy="no-referrer"
                loading="lazy"
                decoding="async"
                class="w-full h-auto block bg-[#F2F2F7]"
                style="aspect-ratio: 3 / 2"
                @error="failedCovers.add(item.code)"
              />
              <div
                v-else
                class="flex flex-col items-center justify-center p-6 text-center bg-gradient-to-br from-[#F2F2F7] to-[#E5E5EA]"
                style="aspect-ratio: 3 / 2"
              >
                <span class="text-[19px] font-semibold text-foreground/60">{{ item.code }}</span>
                <span v-if="item.title" class="mt-1.5 text-[14px] text-foreground-muted/60 line-clamp-3">{{ item.title }}</span>
              </div>
              <span
                v-if="item.in_library"
                class="absolute top-2 left-2 px-2.5 py-1 rounded-full bg-[#30d158] text-white text-[11px] font-semibold shadow-sm"
              >
                已在库
              </span>
            </div>

            <!-- Info -->
            <div class="flex-1 min-w-0 flex flex-col justify-center">
              <div class="flex items-center gap-2 mb-3">
                <span
                  v-if="isHd(item)"
                  class="px-1.5 py-0.5 rounded bg-black/[0.06] text-foreground text-[11px] font-bold"
                >
                  HD
                </span>
                <span
                  v-if="item.source === 'sukebei'"
                  class="px-1.5 py-0.5 rounded bg-[#FF9F0A]/10 text-[#FF9F0A] text-[11px] font-bold"
                >
                  sukebei
                </span>
              </div>
              <h3 class="text-[28px] sm:text-[36px] font-bold text-foreground leading-[1.1] tracking-tight">
                {{ item.code }}
              </h3>
              <p v-if="item.title" class="mt-3 text-[15px] sm:text-[17px] text-foreground-muted leading-relaxed line-clamp-2">
                {{ item.title }}
              </p>
              <p class="mt-3 text-[14px] text-foreground-muted/70">
                <span v-if="item.release_date">{{ fmtDate(item.release_date) }}</span>
                <span v-if="item.views != null"> · {{ formatCount(item.views) }} views</span>
              </p>

              <!-- Actions: copy best magnet + follow actresses -->
              <div class="flex items-center gap-2.5 sm:gap-3 mt-6 flex-wrap">
                <button
                  v-if="bestMagnet(item)"
                  class="flex items-center gap-2 px-5 sm:px-6 py-2.5 rounded-full bg-[#ff375f] text-white text-[15px] font-semibold transition-all hover:brightness-110 active:scale-[0.97] shrink-0"
                  :class="copiedKey === item.code ? '!bg-[#30d158]' : ''"
                  @click="copyMagnet(item, bestMagnet(item)!, item.code)"
                >
                  <svg v-if="copiedKey !== item.code" width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                    <rect x="9" y="9" width="13" height="13" rx="2"/>
                    <path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/>
                  </svg>
                  <svg v-else width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3">
                    <polyline points="20 6 9 17 4 12"/>
                  </svg>
                  <span>{{ copiedKey === item.code ? 'Copied' : 'Copy Magnet' }}</span>
                </button>

                <button
                  v-for="star in item.stars"
                  :key="star.url"
                  class="flex items-center gap-1.5 h-10 px-5 rounded-full text-[14px] font-medium transition-all active:scale-[0.97] disabled:cursor-default border"
                  :class="isFollowed(star.url)
                    ? 'border-[#30d158]/40 bg-[#30d158]/10 text-[#30d158]'
                    : 'border-black/[0.08] text-foreground hover:bg-black/[0.03]'"
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

              <!-- Magnet candidates: hash visible, one-click copy per row -->
              <div v-if="item.magnets?.length" class="mt-5 space-y-2">
                <div
                  v-for="(m, i) in item.magnets"
                  :key="i"
                  class="flex items-center gap-2.5 h-10 pl-3.5 pr-1.5 rounded-xl bg-[#F5F5F7] text-[12px] min-w-0"
                >
                  <span
                    v-if="m.is_hd"
                    class="shrink-0 px-1.5 py-0.5 rounded bg-black/[0.06] text-foreground text-[10px] font-bold"
                  >
                    HD
                  </span>
                  <span v-if="m.resolution" class="shrink-0 font-semibold text-foreground">{{ m.resolution }}</span>
                  <span v-if="m.size" class="shrink-0 text-foreground-muted">{{ m.size }}</span>
                  <span v-if="m.seeds" class="shrink-0 text-foreground-muted">{{ m.seeds }} seeds</span>
                  <span class="flex-1 min-w-0 truncate font-mono text-[11px] text-foreground-muted/50">{{ shortHash(m.magnet) }}</span>
                  <button
                    class="shrink-0 w-7 h-7 rounded-full flex items-center justify-center transition-all active:scale-[0.97]"
                    :class="copiedKey === magnetKey(item, i)
                      ? 'bg-[#30d158] text-white'
                      : 'text-foreground-muted hover:bg-black/[0.06] hover:text-foreground'"
                    title="Copy magnet"
                    @click="copyMagnet(item, m, magnetKey(item, i))"
                  >
                    <svg v-if="copiedKey !== magnetKey(item, i)" width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                      <rect x="9" y="9" width="13" height="13" rx="2"/>
                      <path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/>
                    </svg>
                    <svg v-else width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3">
                      <polyline points="20 6 9 17 4 12"/>
                    </svg>
                  </button>
                </div>
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
import type { SearchResponse, SearchResultItem, SearchResultMagnet, SearchResultStar } from '~/types/api'

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

const copiedKey = ref('')
let copiedTimer: ReturnType<typeof setTimeout> | null = null

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

function fmtDate(dateStr?: string | null): string {
  if (!dateStr) return ''
  const parts = dateStr.split('/')
  if (parts.length === 3) {
    return `${parts[2]}-${parts[1].padStart(2, '0')}-${parts[0].padStart(2, '0')}`
  }
  return dateStr
}

function bestMagnet(item: SearchResultItem): SearchResultMagnet | undefined {
  return item.magnets?.[0]
}

function isHd(item: SearchResultItem): boolean {
  const res = bestMagnet(item)?.resolution?.toLowerCase() ?? item.resolution?.toLowerCase() ?? ''
  return res.includes('1080') || res.includes('4k') || res.includes('fhd')
}

function magnetKey(item: SearchResultItem, i: number) {
  return `${item.code}:${i}`
}

function shortHash(magnet: string): string {
  const m = magnet.match(/xt=urn:btih:([0-9a-fA-F]{40})/)
  return m ? `btih:${m[1].toLowerCase()}` : magnet
}

function copyMagnet(item: SearchResultItem, m: SearchResultMagnet, key: string) {
  navigator.clipboard.writeText(m.magnet).then(() => {
    copiedKey.value = key
    if (copiedTimer) clearTimeout(copiedTimer)
    copiedTimer = setTimeout(() => { copiedKey.value = '' }, 1500)
    track('copy_magnet', { code: item.code, meta: { source: 'search' } })
  }).catch(() => {
    // ignore
  })
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
  if (copiedTimer) clearTimeout(copiedTimer)
})
</script>
