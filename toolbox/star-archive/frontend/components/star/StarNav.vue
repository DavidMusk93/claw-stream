<template>
  <nav class="max-w-7xl mx-auto px-6">
    <div class="flex items-center gap-2 py-3 overflow-x-auto scrollbar-hide">
      <a
        v-for="star in stars"
        :key="star.code"
        :href="`#${star.code.toLowerCase()}`"
        class="shrink-0 px-4 py-1.5 rounded-full text-sm font-medium transition-all duration-200"
        :class="activeStar === star.code
          ? 'bg-ios-blue text-white'
          : 'bg-ios-bg-tertiary text-ios-text-secondary hover:bg-ios-gray-4 hover:text-ios-text-primary'"
        @click.prevent="scrollToStar(star.code)"
      >
        {{ star.name }}
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
  const el = document.getElementById(code.toLowerCase())
  if (el) {
    el.scrollIntoView({ behavior: 'smooth', block: 'start' })
  }
}

// Update active star on scroll
onMounted(() => {
  const observer = new IntersectionObserver(
    (entries) => {
      entries.forEach((entry) => {
        if (entry.isIntersecting) {
          activeStar.value = entry.target.id.toUpperCase()
        }
      })
    },
    { rootMargin: '-30% 0px -60% 0px' }
  )

  props.stars.forEach((star) => {
    const el = document.getElementById(star.code.toLowerCase())
    if (el) observer.observe(el)
  })

  onUnmounted(() => observer.disconnect())
})
</script>
