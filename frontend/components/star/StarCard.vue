<template>
  <section :id="id" class="scroll-mt-32">
    <!-- Section header -->
    <div class="flex items-baseline justify-between mb-4 px-1">
      <div class="flex items-baseline gap-3">
        <h2 class="text-[24px] sm:text-[30px] font-bold text-foreground tracking-tight">
          {{ star.name }}
        </h2>
        <span class="text-[15px] text-foreground-muted font-medium">
          {{ star.titles?.length ?? 0 }}
        </span>
      </div>
      <button
        class="w-10 h-10 rounded-full text-foreground-muted hover:text-[#ff453a] hover:bg-black/[0.04] transition-colors flex items-center justify-center active:scale-[0.97]"
        title="Delete Star"
        @click="onDelete"
      >
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
          <polyline points="3 6 5 6 21 6" />
          <path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2" />
          <line x1="10" y1="11" x2="10" y2="17" />
          <line x1="14" y1="11" x2="14" y2="17" />
        </svg>
      </button>
    </div>

    <!-- Large display -->
    <div
      v-if="activeTitle"
      class="flex flex-col sm:flex-row gap-6 sm:gap-8 mb-5 p-5 sm:p-7 rounded-3xl bg-white border border-black/[0.06] shadow-sm"
    >
      <!-- Large image -->
      <div class="w-full sm:w-[300px] md:w-[360px] lg:w-[440px] shrink-0 rounded-2xl overflow-hidden bg-black shadow-md">
        <img
          v-if="activeTitle.cover_url && !activeImgError"
          :src="activeTitle.cover_url"
          :alt="activeTitle.code"
          class="w-full h-auto block bg-[#F2F2F7]"
          :style="{ aspectRatio: coverAR(activeTitle) }"
          loading="eager"
          decoding="async"
          @error="activeImgError = true"
        />
        <div
          v-else
          class="flex flex-col items-center justify-center p-6 text-center bg-gradient-to-br from-[#F2F2F7] to-[#E5E5EA]"
          :style="{ aspectRatio: coverAR(activeTitle) }"
        >
          <span class="text-[19px] font-semibold text-foreground/60">{{ activeTitle.code }}</span>
          <span class="mt-1.5 text-[14px] text-foreground-muted/60 line-clamp-3">{{ activeTitle.title }}</span>
        </div>
      </div>

      <!-- Info -->
      <div class="flex-1 min-w-0 flex flex-col justify-center">
        <div class="flex items-center gap-2 mb-3">
          <span class="px-2 py-0.5 rounded bg-black text-white text-[11px] font-bold">#{{ activeTitle.number || activeIndex + 1 }}</span>
          <span
            v-if="activeTitle.resolution?.toLowerCase().includes('1080') || activeTitle.resolution?.toLowerCase().includes('4k')"
            class="px-1.5 py-0.5 rounded bg-black/[0.06] text-foreground text-[11px] font-bold"
          >
            HD
          </span>
        </div>
        <h3 class="text-[28px] sm:text-[36px] font-bold text-foreground leading-[1.1] tracking-tight">
          {{ activeTitle.code }}
        </h3>
        <p class="mt-3 text-[15px] sm:text-[17px] text-foreground-muted leading-relaxed">
          {{ activeTitle.title }}
        </p>
        <p v-if="activeTitle.date" class="mt-4 text-[14px] text-foreground-muted/70">
          {{ fmtDate(activeTitle.date) }}
        </p>

        <!-- Action buttons -->
        <div class="flex items-center gap-2.5 sm:gap-3 mt-7 shrink-0">
          <button
            :disabled="!activeTitle.magnet"
            class="flex items-center gap-2 px-5 sm:px-6 py-2.5 rounded-full bg-[#ff375f] text-white text-[15px] font-semibold transition-all hover:brightness-110 disabled:opacity-30 disabled:cursor-not-allowed active:scale-[0.97] shrink-0"
            @click="onPlay"
          >
            <svg width="17" height="17" viewBox="0 0 24 24" fill="currentColor">
              <path d="M8 5v14l11-7z"/>
            </svg>
            <span>Play</span>
          </button>

          <button
            :disabled="!activeTitle.magnet"
            class="flex items-center gap-2 px-5 sm:px-6 py-2.5 rounded-full border border-black/[0.08] text-foreground text-[15px] font-medium transition-all hover:bg-black/[0.03] active:scale-[0.97] disabled:opacity-30 disabled:cursor-not-allowed shrink-0"
            :class="copied ? '!border-[#30d158] !text-[#30d158]' : ''"
            @click="copyMagnet"
          >
            <svg v-if="!copied" width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
              <rect x="9" y="9" width="13" height="13" rx="2"/>
              <path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/>
            </svg>
            <svg v-else width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3">
              <polyline points="20 6 9 17 4 12"/>
            </svg>
            <span>{{ copied ? 'Copied' : 'Copy' }}</span>
          </button>

          <button
            class="flex items-center gap-2 px-5 sm:px-6 py-2.5 rounded-full border border-black/[0.08] text-foreground text-[15px] font-medium transition-all hover:bg-black/[0.03] active:scale-[0.97] shrink-0"
            :class="activeLiked ? '!border-[#ff375f]/50 !text-[#ff375f]' : ''"
            :disabled="liking"
            @click="toggleLike"
          >
            <svg width="15" height="15" viewBox="0 0 24 24" :fill="activeLiked ? 'currentColor' : 'none'" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
              <path d="M20.84 4.61a5.5 5.5 0 0 0-7.78 0L12 5.67l-1.06-1.06a5.5 5.5 0 0 0-7.78 7.78l1.06 1.06L12 21.23l7.78-7.78 1.06-1.06a5.5 5.5 0 0 0 0-7.78z"/>
            </svg>
            <span>{{ activeLiked ? 'Liked' : 'Like' }}</span>
          </button>
        </div>
      </div>
    </div>

    <!-- Thumbnail row -->
    <div class="relative group/row">
      <div
        ref="rowRef"
        class="flex gap-3 sm:gap-4 items-start overflow-x-auto scrollbar-hide pb-4 pt-1 px-1 snap-x snap-mandatory"
      >
        <button
          v-for="(title, idx) in star.titles"
          :key="title.code"
          class="relative shrink-0 w-[120px] sm:w-[150px] md:w-[180px] rounded-xl overflow-hidden bg-black transition-all duration-200 snap-start active:scale-[0.97]"
          :class="activeIndex === idx ? 'ring-2 ring-[#ff375f] opacity-100' : 'opacity-70 hover:opacity-100'"
          @click="activeIndex = idx"
        >
          <img
            v-if="title.cover_url && !thumbErrors[title.code]"
            :src="title.cover_thumb_url || `/api/cover/${title.code}?thumb=1`"
            :alt="title.code"
            class="w-full h-auto block bg-[#F2F2F7]"
            :style="{ aspectRatio: coverAR(title) }"
            loading="lazy"
            decoding="async"
            @error="thumbErrors[title.code] = true"
          />
          <div
            v-else
            class="flex items-center justify-center p-2 text-center bg-gradient-to-br from-[#F2F2F7] to-[#E5E5EA]"
            :style="{ aspectRatio: coverAR(title) }"
          >
            <span class="text-[11px] font-semibold text-foreground/60">{{ title.code }}</span>
          </div>
          <div class="absolute bottom-1.5 left-1.5 px-1.5 py-0.5 rounded bg-black/70 text-white text-[10px] font-bold">
            #{{ title.number || idx + 1 }}
          </div>
        </button>
      </div>

      <!-- Fade edges -->
      <div class="pointer-events-none absolute inset-y-0 left-0 w-8 bg-gradient-to-r from-void to-transparent opacity-0 sm:opacity-100" />
      <div class="pointer-events-none absolute inset-y-0 right-0 w-8 bg-gradient-to-l from-void to-transparent opacity-0 sm:opacity-100" />
    </div>
  </section>
</template>

<script setup lang="ts">
import type { Star, Title } from '~/types/api'

const props = defineProps<{
  star: Star
  index?: number
}>()

const emit = defineEmits<{
  (e: 'play', magnet: string): void
  (e: 'deleted', code: string): void
}>()

const { track } = useTrack()
const { add: addLog } = useEventLog()

const id = computed(() => props.star.code.toLowerCase())
const activeIndex = ref(0)
const activeTitle = computed(() => props.star.titles?.[activeIndex.value] || null)

const activeImgError = ref(false)
const thumbErrors = ref<Record<string, boolean>>({})

// Reserve each cover's box at its true aspect ratio so the layout never
// jumps when bytes arrive. Fallback 3/2: virtually all covers are
// ~800x537 landscape jackets, not 2:3 portraits.
function coverAR(t: Title): string {
  return t.cover_w && t.cover_h ? `${t.cover_w} / ${t.cover_h}` : '3 / 2'
}
const copied = ref(false)
const liking = ref(false)
const activeLiked = computed(() => activeTitle.value?.user_liked ?? false)

watch(() => props.star.titles, () => {
  activeIndex.value = 0
  activeImgError.value = false
})

watch(activeIndex, () => {
  activeImgError.value = false
})

function onPlay() {
  if (!activeTitle.value?.magnet) return
  track('play', { code: activeTitle.value.code, star_code: props.star.code })
  addLog({ kind: 'action', title: `Play ${activeTitle.value.code}`, detail: props.star.name, state: 'info' })
  emit('play', activeTitle.value.magnet)
}

async function toggleLike() {
  if (liking.value || !activeTitle.value) return
  liking.value = true
  try {
    const { likeTitle } = useApi()
    const newVal = !activeLiked.value
    await likeTitle(activeTitle.value.code, newVal)
    activeTitle.value.user_liked = newVal
    track(newVal ? 'like' : 'unlike', { code: activeTitle.value.code, star_code: props.star.code })
    addLog({ kind: 'action', title: `${newVal ? 'Liked' : 'Unliked'} ${activeTitle.value.code}`, detail: props.star.name, state: 'success' })
  } catch (e: any) {
    console.error('like failed:', e)
    addLog({ kind: 'action', title: `Like failed: ${activeTitle.value?.code}`, detail: String(e?.message ?? e), state: 'error' })
  } finally {
    liking.value = false
  }
}

function copyMagnet() {
  const magnet = activeTitle.value?.magnet
  if (!magnet) return
  navigator.clipboard.writeText(magnet).then(() => {
    copied.value = true
    setTimeout(() => copied.value = false, 1500)
    track('copy_magnet', { code: activeTitle.value?.code, star_code: props.star.code })
  }).catch(() => {
    // ignore
  })
}

const deleting = ref(false)

async function onDelete() {
  if (!confirm(`Delete star "${props.star.name}" and all associated title data?\n(Cache files will be preserved)`)) {
    return
  }
  deleting.value = true
  try {
    const { deleteStar } = useApi()
    await deleteStar(props.star.code)
    emit('deleted', props.star.code)
  } catch (e: any) {
    alert(e?.message || 'Delete failed')
  } finally {
    deleting.value = false
  }
}

function fmtDate(dateStr?: string): string {
  if (!dateStr) return ''
  const parts = dateStr.split('/')
  if (parts.length === 3) {
    return `${parts[2]}-${parts[1].padStart(2, '0')}-${parts[0].padStart(2, '0')}`
  }
  return dateStr
}
</script>
