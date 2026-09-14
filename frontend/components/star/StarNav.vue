<template>
  <nav class="max-w-7xl mx-auto px-4 sm:px-6">
    <div class="flex items-center gap-2.5 py-3.5 overflow-x-auto scrollbar-hide">
      <a
        v-for="star in stars"
        :key="star.code"
        :href="`#star-${star.code.toLowerCase()}`"
        class="shrink-0 px-5 py-2 rounded-full text-[15px] font-medium transition-all duration-200 border active:scale-[0.97]"
        :class="activeStar === star.code
          ? 'bg-foreground text-white shadow-sm border-transparent'
          : 'text-foreground-muted hover:text-foreground hover:bg-black/[0.04] border-black/[0.04]'"
        @click.prevent="scrollToStar(star.code)"
      >
        {{ star.name }}
        <span
          v-if="star.number"
          class="ml-1 text-[11px]"
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
// Codes whose section element is currently observed. Stars load client-side
// after mount and the roster churns (adds/deletes), so observations must be
// diffed on every refresh: observe new sections, unobserve removed ones
// (a deleted star's detached element would otherwise stay observed).
const observed = new Set<string>()

function syncObserved() {
  if (!observer) return
  const current = new Set(props.stars.map(s => s.code))
  for (const code of observed) {
    if (!current.has(code)) {
      const el = document.getElementById(`star-${code.toLowerCase()}`)
      if (el) observer.unobserve(el)
      observed.delete(code)
    }
  }
  for (const star of props.stars) {
    if (observed.has(star.code)) continue
    const el = document.getElementById(`star-${star.code.toLowerCase()}`)
    if (el) {
      observer.observe(el)
      observed.add(star.code)
    }
  }
}

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
  syncObserved()
})

onUnmounted(() => {
  if (observer) observer.disconnect()
  observed.clear()
})

watch(() => props.stars, () => {
  nextTick(syncObserved)
})
</script>
