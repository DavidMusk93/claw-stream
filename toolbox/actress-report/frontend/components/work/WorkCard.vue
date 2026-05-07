<template>
  <div class="group relative bg-white/5 rounded-xl overflow-hidden border border-white/5 hover:border-white/10 transition-all">
    <div class="aspect-[3/4] relative bg-neutral-900 overflow-hidden">
      <img
        v-if="work.cover_url"
        :src="work.cover_url"
        :alt="work.code"
        class="w-full h-full object-cover group-hover:scale-105 transition-transform duration-500"
        loading="lazy"
      />
      <div v-else class="w-full h-full flex items-center justify-center text-neutral-600 text-sm">
        {{ work.code }}
      </div>
      <div class="absolute top-2 right-2 flex gap-1">
        <span v-if="work.resolution" class="text-xs bg-black/60 text-white px-1.5 py-0.5 rounded">
          {{ work.resolution }}
        </span>
      </div>
    </div>

    <div class="p-3">
      <h3 class="font-semibold text-sm mb-1">{{ work.code }}</h3>
      <p v-if="work.title" class="text-xs text-neutral-400 line-clamp-2 mb-2">{{ work.title }}</p>
      <div class="flex items-center justify-between">
        <span v-if="work.date" class="text-xs text-neutral-500">{{ work.date }}</span>
        <button
          v-if="work.magnet"
          class="text-xs bg-orange-500/20 hover:bg-orange-500/30 text-orange-400 px-3 py-1.5 rounded-lg transition-colors"
          @click="$emit('play', work.magnet)"
        >
          Play
        </button>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
interface Work {
  code: string
  title?: string
  date?: string
  views?: string
  likes?: string
  resolution?: string
  cover_url?: string
  magnet?: string
}

defineProps<{
  work: Work
  actressCode: string
}>()

defineEmits<{
  (e: 'play', magnet: string): void
}>()
</script>
