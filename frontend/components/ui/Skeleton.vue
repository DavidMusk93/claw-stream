<template>
  <div
    class="animate-pulse bg-black/[0.06] rounded-lg"
    :class="props.class"
    :style="style"
  />
</template>

<script setup lang="ts">
const props = defineProps<{
  class?: string
  width?: string
  height?: string
  aspect?: string
}>()

const style = computed(() => {
  const s: Record<string, string> = {}
  if (props.width) s.width = props.width
  if (props.height) s.height = props.height
  if (props.aspect) s.aspectRatio = props.aspect
  return s
})
</script>
