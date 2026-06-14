<template>
  <div
    class="group relative shrink-0 w-[140px] sm:w-[160px] md:w-[180px] lg:w-[200px] cursor-pointer select-none"
    :class="{ 'pointer-events-none': !title.magnet }"
    @click="title.magnet && $emit('play', title.magnet)"
  >
    <!-- Poster -->
    <div class="relative rounded-lg overflow-hidden bg-[#1a1a1a] shadow-md transition-all duration-300 group-hover:shadow-2xl group-hover:scale-105 group-hover:z-10">
      <img
        v-if="title.cover_url && !imgError"
        :src="title.cover_url"
        :alt="title.code"
        class="w-full h-auto block bg-[#F2F2F7]"
        loading="lazy"
        decoding="async"
        @error="imgError = true"
        @load="imgLoaded = true"
      />
      <Skeleton
        v-else-if="!imgError && !imgLoaded"
        class="aspect-[2/3] w-full"
      />
      <div
        v-if="!title.cover_url || imgError"
        class="aspect-[2/3] flex flex-col items-center justify-center p-4 text-center bg-gradient-to-br from-[#2a2a2a] to-[#1a1a1a]"
      >
        <span class="text-[13px] font-semibold text-white/70">{{ title.code }}</span>
        <span class="mt-1 text-[11px] text-white/50 line-clamp-3">{{ title.title }}</span>
      </div>

      <!-- Number badge -->
      <div class="absolute top-2 left-2 z-10 px-1.5 py-0.5 rounded bg-black/70 text-white text-[10px] font-bold">
        #{{ number || index || 0 }}
      </div>

      <!-- HD badge -->
      <div
        v-if="title.resolution?.toLowerCase().includes('1080') || title.resolution?.toLowerCase().includes('4k')"
        class="absolute top-2 right-2 z-10 px-1.5 py-0.5 rounded bg-[#e50914] text-white text-[9px] font-bold"
      >
        HD
      </div>

      <!-- Netflix-style hover overlay -->
      <div
        class="absolute inset-0 flex flex-col justify-end bg-gradient-to-t from-black/95 via-black/70 to-transparent opacity-0 group-hover:opacity-100 transition-opacity duration-300 p-3"
      >
        <div class="flex items-center gap-2 mb-2">
          <button
            :disabled="!title.magnet"
            class="w-9 h-9 rounded-full bg-white text-black flex items-center justify-center transition hover:bg-white/90 active:scale-95 disabled:opacity-30"
            @click.stop="title.magnet && $emit('play', title.magnet)"
          >
            <svg width="18" height="18" viewBox="0 0 24 24" fill="currentColor">
              <path d="M8 5v14l11-7z"/>
            </svg>
          </button>

          <button
            class="w-9 h-9 rounded-full border border-white/70 text-white flex items-center justify-center transition hover:bg-white/20 active:scale-95"
            :class="localLiked ? '!bg-[#e50914] !border-[#e50914] !text-white' : ''"
            @click.stop="toggleLike"
          >
            <svg width="16" height="16" viewBox="0 0 24 24" :fill="localLiked ? 'currentColor' : 'none'" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
              <path d="M20.84 4.61a5.5 5.5 0 0 0-7.78 0L12 5.67l-1.06-1.06a5.5 5.5 0 0 0-7.78 7.78l1.06 1.06L12 21.23l7.78-7.78 1.06-1.06a5.5 5.5 0 0 0 0-7.78z"/>
            </svg>
          </button>
        </div>

        <h3 class="text-white text-[13px] font-bold leading-tight line-clamp-1">
          {{ title.code }}
        </h3>
        <p class="text-white/80 text-[11px] leading-snug line-clamp-4 mt-1">
          {{ title.title }}
        </p>
        <p v-if="title.date" class="text-white/50 text-[10px] mt-1.5">
          {{ fmtDate(title.date) }}
        </p>
      </div>
    </div>

    <!-- Info below card -->
    <div class="mt-2 px-0.5">
      <h3 class="text-[12px] font-semibold text-foreground truncate leading-tight">
        {{ title.code }}
      </h3>
      <p class="text-[11px] text-foreground-muted truncate">
        {{ title.title || '\u00A0' }}
      </p>
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

const imgError = ref(false)
const imgLoaded = ref(false)
const localLiked = computed(() => props.title.user_liked ?? false)
const liking = ref(false)

async function toggleLike() {
  if (liking.value) return
  liking.value = true
  try {
    const { likeTitle } = useApi()
    await likeTitle(props.title.code, !localLiked.value)
    props.title.user_liked = !localLiked.value
  } catch (e: any) {
    console.error('like failed:', e)
  } finally {
    liking.value = false
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
