<template>
  <section :id="id" class="scroll-mt-40">
    <!-- Section header -->
    <div class="flex items-baseline justify-between mb-5 px-1">
      <h2 class="text-[26px] font-bold text-white tracking-tight">
        {{ star.name }}
      </h2>
      <span class="text-[13px] text-[#8e8e93]">
        {{ star.titles?.length ?? 0 }}
      </span>
    </div>

    <!-- Main area: big image + dock -->
    <div class="flex flex-col sm:flex-row gap-4">
      <!-- Hero image -->
      <div class="flex-1 min-w-0">
        <div ref="heroWrap" class="relative rounded-2xl overflow-hidden bg-black">
          <img
            v-if="activeTitle?.cover_url"
            ref="heroImg"
            :src="activeTitle.cover_url"
            :alt="activeTitle.code"
            class="w-full h-auto block"
            loading="lazy"
            @load="onHeroLoad"
          />
          <div
            v-else
            class="w-full aspect-[2/3] flex items-center justify-center text-[#333] text-sm font-medium"
          >
            {{ activeTitle?.code || star.code }}
          </div>
        </div>

        <!-- Action buttons -->
        <div class="flex items-center gap-3 mt-4 px-1">
          <button
            :disabled="!activeTitle?.magnet"
            class="flex items-center gap-2 px-5 py-2.5 rounded-full bg-white text-black text-[14px] font-medium transition-opacity hover:opacity-90 disabled:opacity-30 disabled:cursor-not-allowed"
            @click="activeTitle?.magnet && $emit('play', activeTitle.magnet)"
          >
            <svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor">
              <path d="M8 5v14l11-7z"/>
            </svg>
            <span>播放</span>
          </button>

          <button
            :disabled="!activeTitle?.magnet"
            class="flex items-center gap-2 px-5 py-2.5 rounded-full bg-[#1c1c1e] text-white text-[14px] font-medium transition-colors hover:bg-[#2c2c2e] disabled:opacity-30 disabled:cursor-not-allowed"
            @click="copyMagnet"
          >
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
              <rect x="9" y="9" width="13" height="13" rx="2"/>
              <path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/>
            </svg>
            <span>复制磁力</span>
          </button>
        </div>

        <!-- Title info -->
        <div class="mt-4 px-1">
          <h3 class="text-[17px] font-semibold text-white leading-tight">
            {{ activeTitle?.code }}
          </h3>
          <p class="mt-1 text-[14px] text-[#8e8e93] line-clamp-2 leading-snug">
            {{ activeTitle?.title }}
          </p>
        </div>
      </div>

      <!-- Thumbnail dock -->
      <div
        ref="dockRef"
        class="flex gap-2 shrink-0"
        :class="isMobile ? 'flex-row' : 'flex-col'"
        :style="dockStyle"
      >
        <button
          v-for="(title, idx) in star.titles"
          :key="title.code"
          class="relative rounded-lg overflow-hidden bg-black transition-all duration-200"
          :class="[
            isMobile ? 'flex-1 h-max min-h-[40px]' : 'flex-1 w-max min-w-[60px]',
            activeIndex === idx ? 'ring-2 ring-white opacity-100' : 'opacity-40 hover:opacity-70'
          ]"
          @click="activeIndex = idx"
        >
          <img
            :src="title.cover_url"
            :alt="title.code"
            :class="isMobile ? 'w-full h-auto' : 'h-full w-auto'"
            class="block"
            loading="lazy"
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

defineEmits<{
  (e: 'play', magnet: string): void
}>()

const id = computed(() => props.star.code.toLowerCase())
const activeIndex = ref(0)
const copied = ref(false)

const activeTitle = computed(() => {
  const titles = props.star.titles || []
  return titles[activeIndex.value] || titles[0] || null
})

function fmtDate(dateStr?: string): string {
  if (!dateStr) return ''
  const parts = dateStr.split('/')
  if (parts.length === 3) {
    const year = parts[2].slice(-2)
    const month = parts[0].padStart(2, '0')
    const day = parts[1].padStart(2, '0')
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

// ── Layout sizing: dock aligns to hero image ──
const heroWrap = ref<HTMLElement>()
const heroImg = ref<HTMLImageElement>()
const dockRef = ref<HTMLElement>()

const heroSize = ref({ width: 0, height: 0 })
const isMobile = ref(false)

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

const dockStyle = computed(() => {
  if (isMobile.value) {
    // Mobile: dock total width = hero image width
    return { width: `${heroSize.value.width}px` }
  }
  // Desktop: dock total height = hero image height
  return { height: `${heroSize.value.height}px` }
})

// No fixed thumbStyle needed — flex-1 distributes space,
// and img w-auto/h-auto preserves original aspect ratio exactly.
</script>
