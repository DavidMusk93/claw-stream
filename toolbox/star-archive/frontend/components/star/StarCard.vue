<template>
  <section :id="id" class="scroll-mt-40 ripple-scroll">
    <!-- Section header: elegant and atmospheric -->
    <div class="flex items-end justify-between mb-5">
      <div class="flex items-baseline gap-4">
        <h2 class="text-3xl font-display font-semibold tracking-wide text-gradient-rose">
          {{ star.name }}
        </h2>
        <span v-if="star.jp" class="text-sm text-foreground-muted font-light">
          {{ star.jp }}
        </span>
        <span class="text-xs text-foreground-muted/40 font-mono">{{ star.titles?.length ?? 0 }} works</span>
      </div>
      <span
        v-if="star.note"
        class="text-[11px] text-foreground-muted glass px-4 py-1.5 rounded-full"
      >
        {{ star.note }}
      </span>
    </div>

    <!-- Horizontal scrolling title cards -->
    <div class="flex gap-4 md:gap-5 lg:gap-6 overflow-x-auto scrollbar-hide pb-6 -mx-6 px-6">
      <TitleCard
        v-for="(title, idx) in star.titles"
        :key="title.code"
        :title="title"
        :star-code="star.code"
        :index="idx"
        :number="idx + 1"
        @play="$emit('play', $event)"
      />
    </div>
  </section>
</template>

<script setup lang="ts">
import type { Star } from '~/types/api'

const props = defineProps<{
  star: Star
  index?: number
}>()

defineEmits<{
  (e: 'play', magnet: string): void
}>()

const id = computed(() => props.star.code.toLowerCase())
</script>
