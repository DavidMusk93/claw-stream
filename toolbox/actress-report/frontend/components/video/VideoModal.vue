<template>
  <Teleport to="body">
    <div
      v-if="isOpen"
      class="fixed inset-0 z-[100] flex items-center justify-center bg-black/90 backdrop-blur-sm"
      @click.self="close"
    >
      <div class="relative w-[90vw] max-w-5xl aspect-video bg-black rounded-xl overflow-hidden shadow-2xl border border-white/10">
        <!-- Close button -->
        <button
          class="absolute top-4 right-4 z-20 w-10 h-10 rounded-full bg-black/60 hover:bg-black/80 text-white flex items-center justify-center text-xl transition-colors"
          @click="close"
        >
          ✕
        </button>

        <!-- Video player -->
        <video
          v-show="!loading && !errorMsg"
          ref="videoRef"
          controls
          playsinline
          class="w-full h-full"
          @canplay="onCanplay"
          @waiting="onWaiting"
          @playing="onPlaying"
          @seeking="onSeeking"
          @seeked="onSeeked"
          @error="onError"
        />

        <!-- Loading overlay -->
        <div
          v-if="loading"
          class="absolute inset-0 flex flex-col items-center justify-center gap-4 bg-black/80 z-10"
        >
          <div class="w-10 h-10 rounded-full border-3 border-white/15 border-t-orange-500 animate-spin" />
          <p class="text-white text-sm">
            {{ statusText }}
          </p>
        </div>

        <!-- Error overlay -->
        <div
          v-if="errorMsg"
          class="absolute inset-0 flex flex-col items-center justify-center gap-4 bg-black/80 z-10"
        >
          <p class="text-red-400 text-sm">{{ errorMsg }}</p>
        </div>

        <!-- Buffer status overlay (small, bottom-left) -->
        <div
          v-if="buffering && !loading"
          class="absolute bottom-16 left-4 z-20 bg-black/60 backdrop-blur-sm px-3 py-1.5 rounded-lg text-xs text-white/80"
        >
          {{ statusText }}
        </div>
      </div>
    </div>
  </Teleport>
</template>

<script setup lang="ts">
const isOpen = defineModel<boolean>('open', { default: false })
const props = defineProps<{ hash?: string }>()

const videoRef = ref<HTMLVideoElement>()
const { status, loading, error, canplayFired, startPolling, stopPolling, waitForHeadReady, formatSpeed } = useVideoPlayer()

const buffering = ref(false)
const errorMsg = ref('')

const streamUrl = computed(() => props.hash ? `http://localhost:8765/stream/${props.hash}` : '')

const statusText = computed(() => {
  if (!status.value) return '连接种子...'
  const s = status.value
  if (!s.ready) return '连接种子...'
  if (!s.head_ready) {
    const speed = formatSpeed(s.download_rate)
    const pct = s.progress.toFixed(1)
    const buf = s.local_size ? (s.local_size / 1024 / 1024).toFixed(0) + 'MB' : ''
    return `缓冲中 | ${speed} | 已缓存 ${buf} (${pct}%)`
  }
  if (s.download_rate > 0) {
    return `缓冲中 | ${formatSpeed(s.download_rate)} | 已缓存 ${(s.local_size / 1024 / 1024).toFixed(0)}MB`
  }
  return '准备播放...'
})

watch(() => props.hash, async (hash) => {
  if (!hash || !isOpen.value) return
  errorMsg.value = ''
  canplayFired.value = false
  buffering.value = true

  const ready = await waitForHeadReady(hash)
  if (!ready) {
    errorMsg.value = error.value || '加载失败'
    return
  }

  // Set src and load
  if (videoRef.value) {
    videoRef.value.src = streamUrl.value
    videoRef.value.load()
  }

  startPolling(hash)
})

watch(isOpen, (open) => {
  if (!open) {
    stopPolling()
    if (videoRef.value) {
      videoRef.value.pause()
      videoRef.value.removeAttribute('src')
      videoRef.value.load()
    }
    errorMsg.value = ''
    buffering.value = false
  }
})

function close() {
  isOpen.value = false
}

function onCanplay() {
  canplayFired.value = true
  buffering.value = false
  videoRef.value?.play().catch(() => {})
}

function onWaiting() {
  buffering.value = true
}

function onPlaying() {
  buffering.value = false
}

function onSeeking() {
  buffering.value = true
}

function onSeeked() {
  buffering.value = false
}

function onError() {
  errorMsg.value = '播放失败，文件可能不完整'
  stopPolling()
}

// Keyboard shortcuts
onMounted(() => {
  const handler = (e: KeyboardEvent) => {
    if (!isOpen.value) return
    const v = videoRef.value
    if (!v) return

    if (e.key === 'Escape') {
      e.preventDefault()
      close()
    } else if (e.key === 'ArrowLeft') {
      e.preventDefault()
      v.currentTime = Math.max(0, v.currentTime - 10)
    } else if (e.key === 'ArrowRight') {
      e.preventDefault()
      v.currentTime = Math.min(v.duration || Infinity, v.currentTime + 10)
    } else if (e.key === ' ') {
      e.preventDefault()
      if (v.paused) v.play()
      else v.pause()
    }
  }
  window.addEventListener('keydown', handler)
  onUnmounted(() => window.removeEventListener('keydown', handler))
})
</script>

<style scoped>
.border-3 {
  border-width: 3px;
}
</style>
