<template>
  <div class="group shrink-0 w-40 sm:w-48">
    <!-- Poster -->
    <div class="relative aspect-[2/3] rounded-2xl overflow-hidden bg-ios-bg-secondary mb-3">
      <img
        v-if="title.cover_url"
        :src="title.cover_url"
        :alt="title.code"
        class="w-full h-full object-cover transition-transform duration-500 ease-out group-hover:scale-105"
        loading="lazy"
      />
      <div v-else class="w-full h-full flex items-center justify-center text-ios-text-tertiary text-sm font-medium">
        {{ title.code }}
      </div>

      <!-- Resolution badge -->
      <div
        v-if="title.resolution"
        class="absolute top-2.5 left-2.5 px-2 py-0.5 rounded-md text-[10px] font-semibold tracking-wide uppercase"
        :class="resolutionBadgeClass"
      >
        {{ title.resolution }}
      </div>

      <!-- Play overlay -->
      <div
        class="absolute inset-0 flex items-center justify-center bg-black/30 opacity-0 group-hover:opacity-100 transition-opacity duration-200"
      >
        <button
          v-if="title.magnet"
          class="w-12 h-12 rounded-full bg-ios-blue/90 backdrop-blur-sm flex items-center justify-center text-white shadow-ios hover:bg-ios-blue transition-colors scale-90 group-hover:scale-100 duration-200"
          @click.stop="$emit('play', title.magnet)"
        >
          <svg width="20" height="20" viewBox="0 0 24 24" fill="currentColor">
            <path d="M8 5v14l11-7z" />
          </svg>
        </button>
      </div>
    </div>

    <!-- Info -->
    <h3 class="text-sm font-semibold truncate mb-0.5">{{ title.code }}</h3>
    <p v-if="title.title" class="text-xs text-ios-text-secondary line-clamp-2 leading-relaxed mb-1.5">
      {{ title.title }}
    </p>
    <span v-if="title.date" class="text-[11px] text-ios-text-tertiary font-mono tabular-nums">
      {{ title.date }}
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

const resolutionBadgeClass = computed(() => {
  const r = props.title.resolution?.toLowerCase() || ''
  if (r.includes('4k')) return 'bg-ios-purple text-white'
  if (r.includes('fhd') || r.includes('1080')) return 'bg-ios-blue text-white'
  if (r.includes('hd') || r.includes('720')) return 'bg-ios-green text-black'
  return 'bg-ios-bg-tertiary text-ios-text-secondary'
})
</script>
