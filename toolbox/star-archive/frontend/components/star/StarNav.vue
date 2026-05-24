<template>
  <nav class="max-w-7xl mx-auto px-4 sm:px-6">
    <div class="flex items-center gap-2 py-3 overflow-x-auto scrollbar-hide">
      <a
        v-for="star in stars"
        :key="star.code"
        :href="`#${star.code.toLowerCase()}`"
        class="shrink-0 px-4 py-1.5 rounded-full text-[13px] font-medium transition-colors duration-200"
        :class="activeStar === star.code
          ? 'bg-white text-black'
          : 'text-[#8e8e93] hover:text-white'"
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
  const el = document.getElementById(`star-${code.toLowerCase()}`)
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
    const el = document.getElementById(`star-${star.code.toLowerCase()}`)
    if (el) observer.observe(el)
  })

  onUnmounted(() => observer.disconnect())
})
</script>
