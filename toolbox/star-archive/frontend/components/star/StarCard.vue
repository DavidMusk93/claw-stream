<template>
  <section :id="id" class="scroll-mt-36">
    <!-- Section header: 更极简 -->
    <div class="flex items-end justify-between mb-6">
      <div class="flex items-baseline gap-3">
        <h2 class="text-xl font-semibold tracking-tight">{{ star.name }}</h2>
        <span v-if="star.jp" class="text-sm text-neutral-500">{{ star.jp }}</span>
      </div>
      <span v-if="star.note" class="text-[11px] text-neutral-500 bg-neutral-900/60 px-3 py-1 rounded-full border border-white/5">
        {{ star.note }}
      </span>
    </div>

    <!-- Horizontal scrolling title cards: 更大间距 -->
    <div class="flex gap-6 overflow-x-auto scrollbar-hide pb-4 -mx-6 px-6">
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
