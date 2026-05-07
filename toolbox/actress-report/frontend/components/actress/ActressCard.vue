<template>
  <section :id="id" class="mb-12 scroll-mt-4">
    <div class="flex items-center gap-4 mb-4">
      <h2 class="text-xl font-bold">{{ actress.name }}</h2>
      <span v-if="actress.jp" class="text-sm text-neutral-500">{{ actress.jp }}</span>
      <span v-if="actress.note" class="text-xs text-neutral-600 bg-white/5 px-2 py-1 rounded">{{ actress.note }}</span>
    </div>

    <div class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
      <WorkCard
        v-for="work in actress.works"
        :key="work.code"
        :work="work"
        :actress-code="actress.code"
        @play="$emit('play', $event)"
      />
    </div>
  </section>
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

interface Actress {
  name: string
  jp?: string
  code: string
  note?: string
  works: Work[]
}

const props = defineProps<{
  actress: Actress
}>()

const id = computed(() => props.actress.code.toLowerCase())
</script>
