<template>
  <div class="group">
    <!-- 封面区域：自适应图片比例，完整展示 -->
    <div class="relative rounded-2xl overflow-hidden bg-neutral-900 mb-2.5">
      <img
        v-if="title.cover_url"
        :src="title.cover_url"
        :alt="title.code"
        class="w-full h-auto block transition-transform duration-500 ease-out group-hover:scale-[1.02]"
        loading="lazy"
      />
      <div
        v-else
        class="w-full aspect-[2/3] flex items-center justify-center text-neutral-500 text-sm font-medium"
      >
        {{ title.code }}
      </div>

      <!-- 分辨率徽章 -->
      <div
        v-if="title.resolution"
        class="absolute top-2 right-2 px-2 py-0.5 rounded-md text-[10px] font-semibold tracking-wide uppercase"
        :class="resolutionBadgeClass"
      >
        {{ title.resolution }}
      </div>

      <!-- 播放遮罩 -->
      <div
        class="absolute inset-0 flex items-center justify-center bg-black/40 opacity-0 group-hover:opacity-100 transition-opacity duration-200"
      >
        <button
          v-if="title.magnet"
          class="w-12 h-12 rounded-full bg-[#0A84FF]/90 backdrop-blur-sm flex items-center justify-center text-white shadow-lg hover:bg-[#0A84FF] transition-colors scale-90 group-hover:scale-100 duration-200"
          @click.stop="$emit('play', title.magnet)"
        >
          <svg width="20" height="20" viewBox="0 0 24 24" fill="currentColor">
            <path d="M8 5v14l11-7z" />
          </svg>
        </button>
      </div>
    </div>

    <!-- 信息区 -->
    <h3 class="text-sm font-semibold truncate">{{ title.code }}</h3>
    <p
      v-if="title.title"
      class="text-xs text-neutral-400 line-clamp-2 leading-relaxed mt-0.5"
    >
      {{ title.title }}
    </p>
    <span
      v-if="title.date"
      class="text-[11px] text-neutral-500 font-mono tabular-nums mt-1 block"
    >
      {{ formatDate(title.date) }}
    </span>
  </div>
</template>

<script setup lang="ts">
import type { Title } from '~/types/api'

const props = defineProps<{
  title: Title
  starCode: string
}>()

defineEmits<{
  (e: 'play', magnet: string): void
}>()

function formatDate(dateStr: string): string {
  // dd/mm/YYYY → YY/mm/dd
  const parts = dateStr.split('/')
  if (parts.length === 3) {
    const yy = parts[2].slice(-2)
    return `${yy}/${parts[1]}/${parts[0]}`
  }
  return dateStr
}

const resolutionBadgeClass = computed(() => {
  const r = props.title.resolution?.toLowerCase() || ''
  if (r.includes('4k')) return 'bg-purple-500 text-white'
  if (r.includes('fhd') || r.includes('1080')) return 'bg-[#0A84FF] text-white'
  if (r.includes('hd') || r.includes('720')) return 'bg-green-500 text-black'
  return 'bg-neutral-700 text-neutral-300'
})
</script>
