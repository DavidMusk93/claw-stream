<template>
  <section :id="id" class="mb-12 scroll-mt-4">
    <div class="flex items-center gap-4 mb-4">
      <h2 class="text-xl font-bold">{{ star.name }}</h2>
      <span v-if="star.jp" class="text-sm text-neutral-500">{{ star.jp }}</span>
      <span v-if="star.note" class="text-xs text-neutral-600 bg-white/5 px-2 py-1 rounded">{{ star.note }}</span>
    </div>

    <div class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
      <TitleCard
        v-for="title in star.titles"
        :key="title.code"
        :title="title"
        :star-code="star.code"
        @play="$emit('play', $event)"
      />
    </div>
  </section>
</template>

<script setup lang="ts">
interface Title {
  code: string
  title?: string
  date?: string
  views?: string
  likes?: string
  resolution?: string
  cover_url?: string
  magnet?: string
}

interface Star {
  name: string
  jp?: string
  code: string
  note?: string
  titles: Title[]
}

const props = defineProps<{
  star: Star
}>()

const id = computed(() => props.star.code.toLowerCase())
</script>
