<template>
  <section :id="id" class="scroll-mt-40">
    <!-- Section header -->
    <div class="flex items-baseline justify-between mb-5 px-1">
      <h2 class="text-[26px] font-bold text-foreground tracking-tight">
        {{ star.name }}
      </h2>
      <div class="flex items-center gap-3">
        <button
          class="text-foreground-muted hover:text-[#ff453a] transition-colors"
          title="Delete Star"
          @click="onDelete"
        >
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <polyline points="3 6 5 6 21 6" />
            <path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2" />
            <line x1="10" y1="11" x2="10" y2="17" />
            <line x1="14" y1="11" x2="14" y2="17" />
          </svg>
        </button>
        <span class="text-[13px] text-foreground-muted">
          {{ star.titles?.length ?? 0 }}
        </span>
      </div>
    </div>

    <!-- Main area: big image + dock -->
    <div class="flex flex-col sm:flex-row gap-4">
      <!-- Hero image -->
      <div class="flex-1 min-w-0">
        <div ref="heroWrap" class="relative rounded-2xl overflow-hidden bg-black">
          <img
            v-if="activeTitle?.cover_url && !heroError"
            ref="heroImg"
            :src="activeTitle.cover_url"
            :alt="activeTitle.code"
            class="w-full h-auto block"
            loading="lazy"
            decoding="async"
            @load="onHeroLoad"
            @error="heroError = true"
          />
          <div
            v-if="!activeTitle?.cover_url || heroError"
            class="w-full aspect-[2/3] flex items-center justify-center text-[#999] text-sm font-medium"
          >
            {{ activeTitle?.code || star.code }}
          </div>
        </div>

        <!-- Action buttons -->
        <div class="flex items-center gap-3 mt-4 px-1">
          <button
            :disabled="!activeTitle?.magnet"
            class="flex items-center gap-2 px-5 py-2.5 rounded-full bg-foreground text-white text-[14px] font-medium transition-opacity hover:opacity-90 disabled:opacity-30 disabled:cursor-not-allowed"
            @click="activeTitle?.magnet && $emit('play', activeTitle.magnet)"
          >
            <svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor">
              <path d="M8 5v14l11-7z"/>
            </svg>
            <span>Play</span>
          </button>

          <button
            :disabled="!activeTitle?.magnet"
            class="flex items-center gap-2 px-6 py-2.5 rounded-full text-[14px] font-medium transition-all disabled:opacity-30 disabled:cursor-not-allowed"
            :class="copied
              ? 'bg-[#30d158] text-black'
              : 'bg-[#F2F2F7] text-foreground hover:bg-[#E5E5EA]'"
            @click="copyMagnet"
          >
            <svg v-if="!copied" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
              <rect x="9" y="9" width="13" height="13" rx="2"/>
              <path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/>
            </svg>
            <svg v-else width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3">
              <polyline points="20 6 9 17 4 12"/>
            </svg>
            <span>{{ copied ? 'Copied ✓' : 'Copy Magnet 🔗' }}</span>
          </button>

          <button
            class="flex items-center gap-2 px-5 py-2.5 rounded-full text-[14px] font-medium transition-all"
            :class="localLiked
              ? 'bg-[#ff375f]/20 text-[#ff375f]'
              : 'bg-[#F2F2F7] text-foreground hover:bg-[#E5E5EA]'"
            :disabled="liking"
            @click="toggleLike"
          >
            <svg width="16" height="16" viewBox="0 0 24 24" :fill="localLiked ? 'currentColor' : 'none'" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
              <path d="M20.84 4.61a5.5 5.5 0 0 0-7.78 0L12 5.67l-1.06-1.06a5.5 5.5 0 0 0-7.78 7.78l1.06 1.06L12 21.23l7.78-7.78 1.06-1.06a5.5 5.5 0 0 0 0-7.78z"/>
            </svg>
            <span>{{ localLiked ? 'Liked' : 'Like' }}</span>
          </button>
        </div>

        <!-- Title info -->
        <div class="mt-4 px-1">
          <h3 class="text-[17px] font-semibold text-foreground leading-tight">
            {{ activeTitle?.code }}
          </h3>
          <p class="mt-1 text-[14px] text-foreground-muted line-clamp-2 leading-snug">
            {{ activeTitle?.title }}
          </p>
        </div>
      </div>

      <!-- Thumbnail dock: 3 visible, swipeable for more -->
      <div
        ref="dockRef"
        class="flex gap-2 shrink-0 scrollbar-hide"
        :class="[
          isMobile
            ? 'flex-row overflow-x-auto snap-x snap-mandatory'
            : 'flex-col overflow-y-auto snap-y snap-mandatory'
        ]"
        :style="dockStyle"
      >
        <button
          v-for="(title, idx) in star.titles"
          :key="title.code"
          class="relative rounded-lg overflow-hidden bg-black transition-all duration-200 snap-start shrink-0"
          :class="activeIndex === idx ? 'ring-2 ring-foreground opacity-100' : 'opacity-40 hover:opacity-70'"
          :style="thumbStyle"
          @click="activeIndex = idx"
        >
          <img
            v-if="title.cover_url && !thumbErrors[title.code]"
            :src="title.cover_url"
            :alt="title.code"
            :class="isMobile ? 'w-full h-auto' : 'h-full w-auto min-w-[1px]'"
            class="block"
            loading="lazy"
            decoding="async"
            @error="thumbErrors[title.code] = true"
          />
          <!-- Number + Date badge -->
          <div class="absolute bottom-1.5 left-1.5 right-1.5 flex items-center justify-between gap-1">
            <span class="px-1.5 py-0.5 rounded text-[10px] font-bold bg-black/70 text-white">
              #{{ title.number }}
            </span>
            <span v-if="title.date" class="px-1.5 py-0.5 rounded text-[10px] font-bold bg-black/70 text-white">
              {{ fmtDate(title.date) }}
            </span>
          </div>
        </button>
      </div>
    </div>
  </section>
</template>

<script setup lang="ts">
import type { Star } from '~/types/api'

const props = defineProps<{
  star: Star
  index?: number
}>()

const emit = defineEmits<{
  (e: 'play', magnet: string): void
  (e: 'deleted', code: string): void
}>()

const id = computed(() => props.star.code.toLowerCase())
const activeIndex = ref(0)
const copied = ref(false)
const heroError = ref(false)
const thumbErrors = ref<Record<string, boolean>>({})
const liking = ref(false)

const activeTitle = computed(() => {
  const titles = props.star.titles || []
  return titles[activeIndex.value] || titles[0] || null
})

const localLiked = computed(() => activeTitle.value?.user_liked ?? false)

async function toggleLike() {
  if (liking.value || !activeTitle.value) return
  liking.value = true
  try {
    const { likeTitle } = useApi()
    const newVal = !localLiked.value
    await likeTitle(activeTitle.value.code, newVal)
    activeTitle.value.user_liked = newVal
  } catch (e: any) {
    alert(e?.data?.detail || 'Action failed')
  } finally {
    liking.value = false
  }
}

function fmtDate(dateStr?: string): string {
  if (!dateStr) return ''
  const parts = dateStr.split('/')
  if (parts.length === 3) {
    const year = parts[2].slice(-2)
    const day = parts[0].padStart(2, '0')
    const month = parts[1].padStart(2, '0')
    return `${year}/${month}/${day}`
  }
  return dateStr
}

function copyMagnet() {
  const magnet = activeTitle.value?.magnet
  if (!magnet) return
  navigator.clipboard.writeText(magnet).then(() => {
    copied.value = true
    setTimeout(() => copied.value = false, 1500)
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

// ── Layout sizing: dock aligns to hero image ──
const heroWrap = ref<HTMLElement>()
const heroImg = ref<HTMLImageElement>()
const dockRef = ref<HTMLElement>()

const heroSize = ref({ width: 0, height: 0 })
const isMobile = ref(false)

// Infer mobile from user-agent during SSR to reduce hydration mismatch
if (import.meta.server) {
  const ua = useRequestHeader('user-agent') || ''
  isMobile.value = /Mobile|Android|iPhone|iPad|iPod/i.test(ua)
}

function updateHeroSize() {
  const img = heroImg.value
  if (img && img.complete && img.naturalWidth > 0) {
    heroSize.value = { width: img.offsetWidth, height: img.offsetHeight }
  }
}

function onHeroLoad() {
  updateHeroSize()
}

function checkBreakpoint() {
  isMobile.value = window.innerWidth < 640
}

let ro: ResizeObserver | null = null

onMounted(() => {
  checkBreakpoint()
  window.addEventListener('resize', checkBreakpoint)

  // Handle cached image (load event may have fired before listener attached)
  nextTick(() => updateHeroSize())

  if (heroWrap.value) {
    ro = new ResizeObserver(() => updateHeroSize())
    ro.observe(heroWrap.value)
  }
})

onUnmounted(() => {
  window.removeEventListener('resize', checkBreakpoint)
  if (ro) ro.disconnect()
})

// Re-measure when active image changes
watch(() => activeTitle.value?.cover_url, () => {
  nextTick(() => {
    // Give browser time to swap the img src
    requestAnimationFrame(() => updateHeroSize())
  })
})

const gap = 8 // tailwind gap-2 = 0.5rem = 8px

const SSR_DEFAULT_HERO_HEIGHT = 480

const dockStyle = computed(() => {
  if (!import.meta.client && !isMobile.value) {
    return { height: `${SSR_DEFAULT_HERO_HEIGHT}px` }
  }
  if (isMobile.value) {
    return { width: `${heroSize.value.width}px` }
  }
  return { height: `${heroSize.value.height}px` }
})

const thumbCount = 3 // Number of thumbs aligned with the hero

const thumbStyle = computed(() => {
  if (!import.meta.client) {
    const h = (SSR_DEFAULT_HERO_HEIGHT - gap * (thumbCount - 1)) / thumbCount
    return { width: 'auto', height: `${h}px`, minWidth: `${Math.round(h * 2 / 3)}px` }
  }
  if (isMobile.value) {
    const w = heroSize.value.width > 0
      ? (heroSize.value.width - gap * (thumbCount - 1)) / thumbCount
      : 120
    return { width: `${w}px`, height: 'auto' }
  }
  const h = heroSize.value.height > 0
    ? (heroSize.value.height - gap * (thumbCount - 1)) / thumbCount
    : 160
  const w = heroSize.value.height > 0 ? 'auto' : `${Math.round(h * 2 / 3)}px`
  return { width: w, height: `${h}px`, minWidth: `${Math.round(h * 2 / 3)}px` }
})
</script>
