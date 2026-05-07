<template>
  <section :id="id" class="scroll-mt-36">
    <!-- Section header -->
    <div class="flex items-end justify-between mb-5">
      <div class="flex items-baseline gap-3">
        <h2 class="text-2xl font-bold tracking-tight">{{ star.name }}</h2>
        <span v-if="star.jp" class="text-sm text-ios-text-secondary">{{ star.jp }}</span>
      </div>
      <span v-if="star.note" class="text-xs text-ios-text-tertiary bg-ios-bg-tertiary px-3 py-1 rounded-full">
        {{ star.note }}
      </span>
    </div>

    <!-- Horizontal scrolling title cards -->
    <div class="flex gap-4 overflow-x-auto scrollbar-hide pb-2 -mx-6 px-6">
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
import type { Star } from '~/types/api'

const props = defineProps<{
  star: Star
}>()

defineEmits<{
  (e: 'play', magnet: string): void
}>()

const id = computed(() => props.star.code.toLowerCase())
</script>
