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
        @error="imgError = true"
      />
      <div
        v-if="!title.cover_url || imgError"
        class="w-full h-full flex items-center justify-center text-[#333] text-sm font-medium"
      >
        {{ title.code }}
      </div>

      <!-- Play overlay -->
      <div
        v-if="title.magnet"
        class="absolute inset-0 opacity-0 group-hover:opacity-100 transition-opacity duration-200 bg-black/25 flex items-center justify-center"
      >
        <div class="w-12 h-12 rounded-full bg-white/25 backdrop-blur-md flex items-center justify-center text-white scale-90 group-hover:scale-100 transition-transform duration-200">
          <svg width="20" height="20" viewBox="0 0 24 24" fill="currentColor">
            <path d="M8 5v14l11-7z"/>
          </svg>
        </div>
      </div>
    </div>

    <!-- Info: show original content only -->
    <div class="mt-3 px-0.5">
      <h3 class="text-[15px] font-semibold text-white truncate leading-tight">
        {{ title.code }}
      </h3>
      <p
        v-if="title.title"
        class="mt-0.5 text-[14px] text-[#8e8e93] line-clamp-2 leading-snug"
      >
        {{ title.title }}
      </p>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref } from 'vue'
import type { Title } from '~/types/api'

defineProps<{
  title: Title
  starCode: string
  index?: number
  number?: number
}>()

defineEmits<{
  (e: 'play', magnet: string): void
}>()

const imgError = ref(false)
</script>
