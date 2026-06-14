<template>
  <nav class="max-w-7xl mx-auto px-4 sm:px-6">
    <div class="flex items-center gap-2 py-3 overflow-x-auto scrollbar-hide">
      <a
        v-for="star in stars"
        :key="star.code"
        :href="`#star-${star.code.toLowerCase()}`"
        class="shrink-0 px-4 py-1.5 rounded-full text-[13px] font-medium transition-all duration-200 border border-transparent"
        :class="activeStar === star.code
          ? 'bg-foreground text-white shadow-sm'
          : 'text-foreground-muted hover:text-foreground hover:bg-black/[0.04] border-black/[0.04]'"
        @click.prevent="scrollToStar(star.code)"
      >
        {{ star.name }}
        <span
          v-if="star.number"
          class="ml-1 text-[10px]"
          :class="activeStar === star.code ? 'text-white/70' : 'text-foreground-muted/60'"
        >
          #{{ star.number }}
        </span>
      </a>
    </div>
  </nav>
</template>

<script setup lang="ts">
import type { Star } from '~/types/api'

const props = defineProps<{
  stars: Star[]
}>()

const activeStar = ref('')

function scrollToStar(code: string) {
  activeStar.value = code
  const el = document.getElementById(`star-${code.toLowerCase()}`)
  if (el) {
    el.scrollIntoView({ behavior: 'smooth', block: 'start' })
  }
}

let observer: IntersectionObserver | null = null

onMounted(() => {
  observer = new IntersectionObserver(
    (entries) => {
      entries.forEach((entry) => {
        if (entry.isIntersecting) {
          const idCode = entry.target.id.replace('star-', '').toUpperCase()
          activeStar.value = idCode
        }
      })
    },
    { rootMargin: '-30% 0px -60% 0px' }
  )

  props.stars.forEach((star) => {
    const el = document.getElementById(`star-${star.code.toLowerCase()}`)
    if (el) observer?.observe(el)
  })
})

onUnmounted(() => {
  if (observer) observer.disconnect()
})

watch(() => props.stars, () => {
  nextTick(() => {
    if (!observer) return
    props.stars.forEach((star) => {
      const el = document.getElementById(`star-${star.code.toLowerCase()}`)
      if (el) observer?.observe(el)
    })
  })
})
</script>
