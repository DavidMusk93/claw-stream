<template>
  <div
    class="group cursor-pointer shrink-0 w-[340px] md:w-[440px] lg:w-[520px] xl:w-[600px]"
    :style="{ animationDelay: `${index * 50}ms` }"
    @click="title.magnet && $emit('play', title.magnet)"
  >
    <!-- Cover container: preserves aspect ratio, no crop -->
    <div class="relative rounded-glass overflow-hidden glass-card glow-rose">
      <!-- Image with object-contain to prevent cropping -->
      <div class="relative w-full bg-void/50">
        <img
          v-if="title.cover_url"
          :src="title.cover_url"
          :alt="title.code"
          class="w-full h-auto block image-reveal"
          loading="lazy"
          style="aspect-ratio: auto;"
        />
        <div
          v-else
          class="w-full aspect-[2/3] flex items-center justify-center text-foreground-muted/30 text-sm font-medium"
        >
          {{ title.code }}
        </div>
      </div>

      <!-- Number badge -->
      <div
        class="absolute top-3 left-3 px-2.5 py-1 rounded-full text-[11px] font-bold font-mono glass-strong text-rose"
      >
        #{{ number }}
      </div>

      <!-- Resolution badge -->
      <div
        v-if="title.resolution"
        class="absolute top-3 right-3 px-2.5 py-1 rounded-full text-[10px] font-medium tracking-wider uppercase glass-strong"
        :class="resolutionBadgeClass"
      >
        {{ title.resolution }}
      </div>

      <!-- Hover overlay with play button -->
      <div class="absolute inset-0 opacity-0 group-hover:opacity-100 transition-all duration-500">
        <div class="absolute inset-0 bg-gradient-to-t from-black/80 via-black/20 to-transparent" />
        <div class="absolute inset-0 flex items-center justify-center">
          <button
            v-if="title.magnet"
            class="w-16 h-16 rounded-full glass-strong flex items-center justify-center text-white ring-1 ring-white/20 scale-90 group-hover:scale-100 transition-transform duration-500 hover:bg-rose/20 hover:ring-rose/30"
            @click.stop="$emit('play', title.magnet)"
          >
            <svg width="24" height="24" viewBox="0 0 24 24" fill="currentColor">
              <path d="M8 5v14l11-7z"/>
            </svg>
          </button>
        </div>
      </div>

      <!-- Copy magnet button -->
      <button
        v-if="title.magnet"
        class="absolute bottom-3 left-3 w-8 h-8 rounded-full glass-strong flex items-center justify-center text-white/60 hover:text-white hover:bg-white/10 transition-all"
        @click.stop="copyMagnet"
      >
        <svg
          v-if="!copied"
          width="14"
          height="14"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          stroke-width="2"
        >
          <rect x="9" y="9" width="13" height="13" rx="2"/>
          <path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/>
        </svg>
        <svg
          v-else
          width="14"
          height="14"
          viewBox="0 0 24 24"
          fill="none"
          stroke="#4ADE80"
          stroke-width="2"
        >
          <polyline points="20 6 9 17 4 12"/>
        </svg>
      </button>
    </div>

    <!-- Info section -->
    <div class="mt-4 space-y-1.5">
      <h3 class="text-base font-medium tracking-tight text-foreground truncate">
        {{ title.code }}
      </h3>
      <p
        v-if="title.charming_intro"
        class="text-xs text-foreground-muted line-clamp-2 leading-relaxed font-light"
      >
        {{ title.charming_intro }}
      </p>
      <div class="flex items-center gap-3 pt-1">
        <span
          v-if="title.date"
          class="text-[11px] text-foreground-muted/60 font-mono tabular-nums"
        >
          {{ formatDate(title.date) }}
        </span>
        <span
          v-if="cacheStatus"
          class="text-[10px] font-medium px-2 py-0.5 rounded-full"
          :class="cacheStatus.class"
        >
          {{ cacheStatus.text }}
        </span>
      </div>
      <!-- Progress bar -->
      <div
        v-if="cacheStatus?.progress !== undefined && cacheStatus.progress > 0 && cacheStatus.progress < 100"
        class="h-0.5 bg-white/5 rounded-full overflow-hidden"
      >
        <div
          class="h-full bg-gradient-to-r from-rose to-violet rounded-full transition-all duration-700"
          :style="{ width: cacheStatus.progress + '%' }"
        />
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import type { Title } from '~/types/api'

const props = defineProps<{
  title: Title
  starCode: string
  index?: number
  number?: number
}>()

defineEmits<{
  (e: 'play', magnet: string): void
}>()

const copied = ref(false)

function copyMagnet() {
  if (!props.title.magnet) return
  navigator.clipboard.writeText(props.title.magnet).then(() => {
    copied.value = true
    setTimeout(() => copied.value = false, 1500)
  })
}

function formatDate(dateStr: string): string {
  // Input: dd/mm/YYYY, output: YY/mm/dd
  const parts = dateStr.split('/')
  if (parts.length === 3) {
    return `${parts[2].slice(-2)}/${parts[1]}/${parts[0]}`
  }
  return dateStr
}

// Cache status polling
const cacheStatus = ref<{ text: string; class: string; progress?: number } | null>(null)

const resolutionBadgeClass = computed(() => {
  const r = props.title.resolution?.toLowerCase() || ''
  if (r.includes('4k') || r.includes('2160')) {
    return 'text-amber border border-amber/20 bg-amber/10'
  }
  if (r.includes('1080') || r.includes('fhd')) {
    return 'text-rose border border-rose/20 bg-rose/10'
  }
  return 'text-foreground-muted border border-white/10 bg-white/5'
})

let pollTimer: ReturnType<typeof setInterval> | null = null

onMounted(() => {
  if (!props.title.magnet) return
  const hash = props.title.magnet.match(/xt=urn:btih:([a-f0-9]{40})/i)?.[1]
  if (!hash) return

  async function check() {
    try {
      const res = await $fetch(`/torrent/status/${hash}`) as any
      if (!res) return
      const progress = res.progress || 0
      if (progress >= 100) {
        cacheStatus.value = { text: '已缓存', class: 'text-emerald-400 bg-emerald-400/10 border border-emerald-400/20' }
      } else if (progress > 0) {
        cacheStatus.value = {
          text: `${progress.toFixed(0)}%`,
          class: 'text-rose bg-rose/10 border border-rose/20',
          progress,
        }
      } else if (res.peers > 0) {
        cacheStatus.value = { text: '连接中', class: 'text-amber bg-amber/10 border border-amber/20' }
      }
    } catch {
      // ignore
    }
  }

  check()
  pollTimer = setInterval(check, 5000)
})

onUnmounted(() => {
  if (pollTimer) clearInterval(pollTimer)
})
</script>
