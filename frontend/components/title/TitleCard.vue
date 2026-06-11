<template>
  <div
    class="group shrink-0 w-[240px] sm:w-[280px] md:w-[320px] lg:w-[360px]"
    :class="{ 'cursor-pointer': !!title.magnet }"
    @click="title.magnet && $emit('play', title.magnet)"
  >
    <!-- Cover: full image display, black background blends with page -->
    <div class="relative rounded-2xl overflow-hidden aspect-[2/3] bg-black">
      <img
        v-if="title.cover_url && !imgError"
        :src="title.cover_url"
        :alt="title.code"
        class="w-full h-full object-contain block"
        loading="lazy"
        decoding="async"
        @error="imgError = true"
      />
      <div
        v-if="!title.cover_url || imgError"
        class="w-full h-full flex items-center justify-center text-[#333] text-sm font-medium"
      >
        {{ title.code }}
      </div>

      <!-- Like button (top-right) -->
      <button
        class="absolute top-2 right-2 z-10 w-8 h-8 rounded-full bg-white/40 backdrop-blur-md flex items-center justify-center transition-all hover:bg-white/60 active:scale-90"
        :class="localLiked ? 'text-[#ff375f]' : 'text-foreground/70'"
        @click.stop="toggleLike"
      >
        <svg width="16" height="16" viewBox="0 0 24 24" :fill="localLiked ? 'currentColor' : 'none'" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
          <path d="M20.84 4.61a5.5 5.5 0 0 0-7.78 0L12 5.67l-1.06-1.06a5.5 5.5 0 0 0-7.78 7.78l1.06 1.06L12 21.23l7.78-7.78 1.06-1.06a5.5 5.5 0 0 0 0-7.78z"/>
        </svg>
      </button>

      <!-- Play overlay -->
      <div
        v-if="title.magnet"
        class="absolute inset-0 opacity-0 group-hover:opacity-100 transition-opacity duration-200 bg-black/25 flex items-center justify-center"
      >
        <div class="w-12 h-12 rounded-full bg-white/25 backdrop-blur-md flex items-center justify-center text-foreground scale-90 group-hover:scale-100 transition-transform duration-200">
          <svg width="20" height="20" viewBox="0 0 24 24" fill="currentColor">
            <path d="M8 5v14l11-7z"/>
          </svg>
        </div>
      </div>
    </div>

    <!-- Info: show original content only -->
    <div class="mt-3 px-0.5">
      <h3 class="text-[15px] font-semibold text-foreground truncate leading-tight">
        {{ title.code }}
      </h3>
      <p
        v-if="title.title"
        class="mt-0.5 text-[14px] text-foreground-muted line-clamp-2 leading-snug"
      >
        {{ title.title }}
      </p>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed } from 'vue'
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

const imgError = ref(false)
const localLiked = computed(() => props.title.user_liked ?? false)
const liking = ref(false)

async function toggleLike() {
  if (liking.value) return
  liking.value = true
  try {
    const { likeTitle } = useApi()
    await likeTitle(props.title.code, !localLiked.value)
    // Optimistic update: modify local data directly to avoid waiting for refresh
    props.title.user_liked = !localLiked.value
  } catch (e: any) {
    alert(e?.data?.detail || 'Action failed')
  } finally {
    liking.value = false
  }
}
</script>
