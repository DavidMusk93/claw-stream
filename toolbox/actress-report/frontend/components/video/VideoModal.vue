<template>
  <Teleport to="body">
    <div
      v-if="isOpen"
      class="fixed inset-0 z-[100] flex items-center justify-center bg-black/90 backdrop-blur-sm"
      @click.self="close"
    >
      <div class="relative w-[90vw] max-w-5xl aspect-video bg-black rounded-xl overflow-hidden shadow-2xl">
        <button
          class="absolute top-4 right-4 z-10 w-10 h-10 rounded-full bg-black/50 hover:bg-black/70 text-white flex items-center justify-center transition-colors"
          @click="close"
        >
          ✕
        </button>

        <video
          v-if="streamUrl"
          ref="videoRef"
          :src="streamUrl"
          controls
          playsinline
          autoplay
          class="w-full h-full"
        />

        <div v-else class="w-full h-full flex items-center justify-center text-neutral-400">
          Loading video...
        </div>
      </div>
    </div>
  </Teleport>
</template>

<script setup lang="ts">
const isOpen = defineModel<boolean>('open', { default: false })

const props = defineProps<{
  hash?: string
}>()

const videoRef = ref<HTMLVideoElement>()

const streamUrl = computed(() => {
  if (!props.hash) return ''
  return `http://localhost:8765/stream/${props.hash}`
})

function close() {
  isOpen.value = false
  if (videoRef.value) {
    videoRef.value.pause()
    videoRef.value.src = ''
  }
}

watch(() => props.hash, (newHash) => {
  if (newHash && videoRef.value) {
    videoRef.value.load()
  }
})
</script>
