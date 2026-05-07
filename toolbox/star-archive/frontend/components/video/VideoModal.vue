<template>
  <Teleport to="body">
    <div
      v-if="isOpen"
      class="fixed inset-0 z-[100] flex items-center justify-center bg-black/95 backdrop-blur-sm"
      @click.self="close"
    >
      <div
        ref="containerRef"
        class="relative w-full h-full sm:w-[90vw] sm:max-w-5xl sm:h-auto sm:aspect-video bg-black sm:rounded-xl overflow-hidden"
        @dblclick="togglePlay"
        @touchstart="onTouchStart"
        @touchend="onTouchEnd"
        @touchmove="onTouchMove"
      >
        <!-- Close button -->
        <button
          class="absolute top-3 right-3 z-20 w-12 h-12 rounded-full bg-black/60 active:bg-black/80 text-white flex items-center justify-center text-xl transition-colors touch-manipulation"
          @click="close"
        >
          ✕
        </button>

        <!-- Fullscreen toggle -->
        <button
          class="absolute top-3 left-3 z-20 w-12 h-12 rounded-full bg-black/60 active:bg-black/80 text-white flex items-center justify-center text-lg transition-colors touch-manipulation"
          @click="toggleFullscreen"
        >
          {{ isFullscreen ? '⤓' : '⤢' }}
        </button>

        <!-- Video player -->
        <video
          v-show="!loading && !errorMsg"
          ref="videoRef"
          controls
          playsinline
          webkit-playsinline
          x5-playsinline
          x5-video-player-type="h5"
          x5-video-player-fullscreen="false"
          controlsList="nodownload noremoteplayback"
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
          <div class="w-10 h-10 rounded-full border-4 border-white/15 border-t-orange-500 animate-spin" />
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
          class="absolute bottom-20 left-4 z-20 bg-black/60 backdrop-blur-sm px-3 py-1.5 rounded-lg text-xs text-white/80"
        >
          {{ statusText }}
        </div>

        <!-- Touch gesture hint (mobile only) -->
        <div
          v-if="showGestureHint"
          class="absolute inset-0 flex items-center justify-center z-30 pointer-events-none"
        >
          <div class="bg-black/50 text-white px-4 py-2 rounded-full text-sm animate-fade-out">
            {{ gestureHintText }}
          </div>
        </div>
      </div>
    </div>
  </Teleport>
</template>

<script setup lang="ts">
import { logInfo, logError } from '~/composables/useLogger'

const isOpen = defineModel<boolean>('open', { default: false })
const props = defineProps<{ hash?: string }>()

const videoRef = ref<HTMLVideoElement>()
const containerRef = ref<HTMLDivElement>()
const { status, loading, error, canplayFired, startPolling, stopPolling, waitForHeadReady, formatSpeed } = useVideoPlayer()

const buffering = ref(false)
const errorMsg = ref('')
const isFullscreen = ref(false)

// Touch gesture state
const touchStartX = ref(0)
const touchStartY = ref(0)
const touchStartTime = ref(0)
const showGestureHint = ref(false)
const gestureHintText = ref('')

const streamUrl = computed(() => props.hash ? `/stream/${props.hash}` : '')

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
  logInfo(`[player] open video hash=${hash.slice(0, 12)}`)

  const ready = await waitForHeadReady(hash)
  if (!ready) {
    errorMsg.value = error.value || '加载失败'
    logError(`[player] load failed hash=${hash.slice(0, 12)}: ${errorMsg.value}`)
    return
  }

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
    if (isFullscreen.value) {
      exitFullscreen()
    }
    errorMsg.value = ''
    buffering.value = false
    showGestureHint.value = false
  }
})

function close() {
  logInfo('[player] close video')
  isOpen.value = false
}

function onCanplay() {
  canplayFired.value = true
  buffering.value = false
  logInfo('[player] canplay')
  videoRef.value?.play().catch(() => {})
}

function onWaiting() {
  buffering.value = true
  logInfo('[player] waiting (buffering)')
}
function onPlaying() {
  buffering.value = false
  logInfo('[player] playing')
}
function onSeeking() {
  buffering.value = true
  logInfo('[player] seeking')
}
function onSeeked() {
  buffering.value = false
  logInfo('[player] seeked')
}

function onError() {
  errorMsg.value = '播放失败，文件可能不完整'
  logError('[player] video error:', errorMsg.value)
  stopPolling()
}

function togglePlay() {
  const v = videoRef.value
  if (!v) return
  if (v.paused) {
    v.play().catch(() => {})
    logInfo('[player] toggle play')
  } else {
    v.pause()
    logInfo('[player] toggle pause')
  }
}

function toggleFullscreen() {
  const el = containerRef.value
  if (!el) return
  if (!isFullscreen.value) {
    enterFullscreen(el)
    logInfo('[player] enter fullscreen')
  } else {
    exitFullscreen()
    logInfo('[player] exit fullscreen')
  }
}

function enterFullscreen(el: HTMLElement) {
  const methods = [
    el.requestFullscreen,
    (el as any).webkitRequestFullscreen,
    (el as any).msRequestFullscreen,
  ]
  for (const m of methods) {
    if (m) {
      m.call(el).then(() => { isFullscreen.value = true }).catch(() => {})
      return
    }
  }
}

function exitFullscreen() {
  const doc = document as any
  const methods = [
    document.exitFullscreen,
    doc.webkitExitFullscreen,
    doc.msExitFullscreen,
  ]
  for (const m of methods) {
    if (m) {
      m.call(document).then(() => { isFullscreen.value = false }).catch(() => {})
      return
    }
  }
}

// Listen fullscreen change
onMounted(() => {
  const handler = () => {
    isFullscreen.value = !!(document.fullscreenElement || (document as any).webkitFullscreenElement)
  }
  document.addEventListener('fullscreenchange', handler)
  document.addEventListener('webkitfullscreenchange', handler)
  onUnmounted(() => {
    document.removeEventListener('fullscreenchange', handler)
    document.removeEventListener('webkitfullscreenchange', handler)
  })
})

// Touch gestures
function onTouchStart(e: TouchEvent) {
  const t = e.touches[0]
  touchStartX.value = t.clientX
  touchStartY.value = t.clientY
  touchStartTime.value = Date.now()
}

function onTouchMove(e: TouchEvent) {
  if (e.touches.length !== 1) return
  // Prevent page scroll when touching video
  if (videoRef.value) {
    e.preventDefault()
  }
}

function onTouchEnd(e: TouchEvent) {
  const t = e.changedTouches[0]
  const dx = t.clientX - touchStartX.value
  const dy = t.clientY - touchStartY.value
  const dt = Date.now() - touchStartTime.value
  const v = videoRef.value
  if (!v) return

  // Tap to toggle play (small movement, short time)
  if (Math.abs(dx) < 10 && Math.abs(dy) < 10 && dt < 300) {
    // Let doubleclick handle it, or toggle play if no dblclick
    return
  }

  // Horizontal swipe: seek
  if (Math.abs(dx) > Math.abs(dy) && Math.abs(dx) > 40) {
    const seekSeconds = dx > 0 ? 10 : -10
    const prev = v.currentTime
    v.currentTime = Math.max(0, Math.min(v.duration || Infinity, v.currentTime + seekSeconds))
    logInfo(`[player] swipe seek ${seekSeconds > 0 ? '+' : ''}${seekSeconds}s ${prev.toFixed(1)} -> ${v.currentTime.toFixed(1)}`)
    showHint(dx > 0 ? '快进 10 秒' : '后退 10 秒')
    return
  }

  // Vertical swipe: volume (right side) or brightness (left side)
  if (Math.abs(dy) > 60) {
    if (touchStartX.value > window.innerWidth / 2) {
      const delta = dy < 0 ? 0.1 : -0.1
      const prev = v.volume
      v.volume = Math.max(0, Math.min(1, v.volume + delta))
      logInfo(`[player] swipe volume ${prev.toFixed(2)} -> ${v.volume.toFixed(2)}`)
      showHint(`音量 ${Math.round(v.volume * 100)}%`)
    }
  }
}

function showHint(text: string) {
  gestureHintText.value = text
  showGestureHint.value = true
  setTimeout(() => { showGestureHint.value = false }, 800)
}

// Keyboard shortcuts
onMounted(() => {
  const handler = (e: KeyboardEvent) => {
    if (!isOpen.value) return
    const v = videoRef.value
    if (!v) return

    if (e.key === 'Escape') {
      e.preventDefault()
      logInfo('[player] key Escape')
      close()
    } else if (e.key === 'ArrowLeft') {
      e.preventDefault()
      const prev = v.currentTime
      v.currentTime = Math.max(0, v.currentTime - 10)
      logInfo(`[player] key ArrowLeft seek ${prev.toFixed(1)} -> ${v.currentTime.toFixed(1)}`)
    } else if (e.key === 'ArrowRight') {
      e.preventDefault()
      const prev = v.currentTime
      v.currentTime = Math.min(v.duration || Infinity, v.currentTime + 10)
      logInfo(`[player] key ArrowRight seek ${prev.toFixed(1)} -> ${v.currentTime.toFixed(1)}`)
    } else if (e.key === ' ') {
      e.preventDefault()
      if (v.paused) {
        v.play()
        logInfo('[player] key Space play')
      } else {
        v.pause()
        logInfo('[player] key Space pause')
      }
    } else if (e.key === 'f' || e.key === 'F') {
      e.preventDefault()
      logInfo('[player] key F fullscreen')
      toggleFullscreen()
    }
  }
  window.addEventListener('keydown', handler)
  onUnmounted(() => window.removeEventListener('keydown', handler))
})
</script>

<style scoped>
.touch-manipulation {
  touch-action: manipulation;
}

@keyframes fade-out {
  0% { opacity: 1; }
  70% { opacity: 1; }
  100% { opacity: 0; }
}

.animate-fade-out {
  animation: fade-out 0.8s ease-out forwards;
}

/* Hide native controls on mobile until user interacts */
video::-webkit-media-controls {
  display: flex !important;
}

/* iOS safe area support */
@supports (padding: max(0px)) {
  .pb-safe {
    padding-bottom: max(1rem, env(safe-area-inset-bottom));
  }
}
</style>
