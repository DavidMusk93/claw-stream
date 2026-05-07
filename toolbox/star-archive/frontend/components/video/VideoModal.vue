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
        @dblclick="onDblClick"
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
          @stalled="onStalled"
          @abort="onAbort"
          @error="onError"
          @timeupdate="onTimeUpdate"
          @ended="onEnded"
          @loadedmetadata="onLoadedMetadata"
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
const { status, loading, error, canplayFired, startPolling, stopPolling, waitForHeadReady, reportSeek, formatSpeed } = useVideoPlayer()

const buffering = ref(false)
const errorMsg = ref('')
const isFullscreen = ref(false)
const retryCount = ref(0)
const MAX_RETRIES = 3

// Progress persistence
const PROGRESS_KEY = 'claw_video_progress'
const PROGRESS_SAVE_INTERVAL_MS = 5000
let lastProgressSave = 0

interface ProgressRecord {
  currentTime: number
  duration: number
  updatedAt: number
}

function loadProgressMap(): Record<string, ProgressRecord> {
  try {
    const raw = localStorage.getItem(PROGRESS_KEY)
    return raw ? JSON.parse(raw) : {}
  } catch {
    return {}
  }
}

function saveProgressMap(map: Record<string, ProgressRecord>) {
  try {
    localStorage.setItem(PROGRESS_KEY, JSON.stringify(map))
  } catch {
    // storage full or private mode
  }
}

function saveProgress() {
  const v = videoRef.value
  const hash = props.hash
  if (!v || !hash || !v.duration || v.duration === Infinity) return
  if (v.currentTime < 3) return // don't save very early progress
  if (v.currentTime / v.duration > 0.95) return // nearly finished

  const map = loadProgressMap()
  map[hash] = {
    currentTime: v.currentTime,
    duration: v.duration,
    updatedAt: Date.now(),
  }
  saveProgressMap(map)
}

function restoreProgress() {
  const v = videoRef.value
  const hash = props.hash
  if (!v || !hash) return
  const map = loadProgressMap()
  const rec = map[hash]
  if (!rec || !rec.duration) return
  // Skip if already near the end or if duration changed significantly
  if (rec.currentTime / rec.duration > 0.95) {
    delete map[hash]
    saveProgressMap(map)
    return
  }
  if (v.duration && Math.abs(v.duration - rec.duration) / rec.duration > 0.1) return

  v.currentTime = rec.currentTime
  logInfo('player', `restored progress ${rec.currentTime.toFixed(1)}s / ${rec.duration.toFixed(1)}s`)
}

function clearProgress() {
  const hash = props.hash
  if (!hash) return
  const map = loadProgressMap()
  if (map[hash]) {
    delete map[hash]
    saveProgressMap(map)
  }
}

function cleanupOldProgress() {
  const map = loadProgressMap()
  const cutoff = Date.now() - 30 * 24 * 60 * 60 * 1000 // 30 days
  let changed = false
  for (const key of Object.keys(map)) {
    if (map[key].updatedAt < cutoff) {
      delete map[key]
      changed = true
    }
  }
  if (changed) saveProgressMap(map)
}

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
  logInfo('player', `open video hash=${hash.slice(0, 12)}`)

  const ready = await waitForHeadReady(hash)
  if (!ready) {
    errorMsg.value = error.value || '加载失败'
    logError('player', `load failed hash=${hash.slice(0, 12)}: ${errorMsg.value}`)
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
    saveProgress()
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
  logInfo('player', 'close video')
  isOpen.value = false
}

function onLoadedMetadata() {
  cleanupOldProgress()
  restoreProgress()
}

function onCanplay() {
  canplayFired.value = true
  buffering.value = false
  retryCount.value = 0
  logInfo('player', 'canplay')
  videoRef.value?.play().catch(() => {})
}

function onTimeUpdate() {
  const v = videoRef.value
  const now = Date.now()
  if (now - lastProgressSave > PROGRESS_SAVE_INTERVAL_MS) {
    saveProgress()
    lastProgressSave = now
  }
  if (v && v.duration && isFinite(v.duration)) {
    reportSeek(props.hash || '', v.currentTime, v.duration)
  }
}

function onEnded() {
  logInfo('player', 'ended')
  clearProgress()
}

function onWaiting() {
  buffering.value = true
  logInfo('player', 'waiting (buffering)')
}
function onPlaying() {
  buffering.value = false
  logInfo('player', 'playing')
}
function onSeeking() {
  buffering.value = true
  logInfo('player', 'seeking')
  // 暂停播放，等待数据就绪，避免 seek 到未缓存区域触发解码错误
  videoRef.value?.pause()
}
function onSeeked() {
  buffering.value = false
  logInfo('player', 'seeked')
  // 尝试恢复播放，如果数据不足浏览器会再次进入 waiting
  videoRef.value?.play().catch(() => {})
}

function onStalled() {
  buffering.value = true
  logInfo('player', 'stalled (waiting for data)')
}

function onAbort() {
  logInfo('player', 'abort')
  buffering.value = false
}

function onError() {
  const v = videoRef.value
  const code = v?.error?.code ?? 0
  const message = v?.error?.message ?? 'unknown'
  logError('player', `video error code=${code} msg=${message}`)

  if (retryCount.value < MAX_RETRIES) {
    retryCount.value++
    logInfo('player', `retry ${retryCount.value}/${MAX_RETRIES}`)
    errorMsg.value = `加载中 (${retryCount.value}/${MAX_RETRIES})...`
    const currentSrc = v?.src || streamUrl.value
    if (v) {
      v.src = ''
      v.load()
    }
    setTimeout(() => {
      if (videoRef.value) {
        videoRef.value.src = currentSrc
        videoRef.value.load()
      }
    }, 1000)
    return
  }

  errorMsg.value = '播放失败，文件可能不完整'
  stopPolling()
}

function togglePlay() {
  const v = videoRef.value
  if (!v) return
  if (v.paused) {
    v.play().catch(() => {})
    logInfo('player', 'toggle play')
  } else {
    v.pause()
    logInfo('player', 'toggle pause')
  }
}

function toggleFullscreen() {
  const el = containerRef.value
  if (!el) return
  if (!isFullscreen.value) {
    enterFullscreen(el)
    logInfo('player', 'enter fullscreen')
  } else {
    exitFullscreen()
    logInfo('player', 'exit fullscreen')
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

function onDblClick(e: MouseEvent) {
  const v = videoRef.value
  if (!v) return
  const rect = (e.currentTarget as HTMLElement).getBoundingClientRect()
  const x = e.clientX - rect.left
  const width = rect.width

  if (x < width / 3) {
    // 左侧双击：后退 10 秒
    const prev = v.currentTime
    v.currentTime = Math.max(0, v.currentTime - 10)
    logInfo('player', `dblclick left seek ${prev.toFixed(1)} -> ${v.currentTime.toFixed(1)}`)
    showHint('后退 10 秒')
  } else if (x > width * 2 / 3) {
    // 右侧双击：快进 10 秒
    const prev = v.currentTime
    v.currentTime = Math.min(v.duration || Infinity, v.currentTime + 10)
    logInfo('player', `dblclick right seek ${prev.toFixed(1)} -> ${v.currentTime.toFixed(1)}`)
    showHint('快进 10 秒')
  } else {
    // 中间双击：播放/暂停
    togglePlay()
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

  // Horizontal swipe: seek proportional to swipe distance (3s per px)
  if (Math.abs(dx) > Math.abs(dy) && Math.abs(dx) > 40) {
    const SWIPE_PX_PER_SECOND = 3
    const seekSeconds = Math.round(dx * SWIPE_PX_PER_SECOND)
    const prev = v.currentTime
    v.currentTime = Math.max(0, Math.min(v.duration || Infinity, v.currentTime + seekSeconds))
    const absSec = Math.abs(seekSeconds)
    let hint = ''
    if (absSec >= 60) {
      hint = `${seekSeconds > 0 ? '快进' : '后退'} ${Math.floor(absSec / 60)}分${absSec % 60}秒`
    } else {
      hint = `${seekSeconds > 0 ? '快进' : '后退'} ${absSec}秒`
    }
    logInfo('player', `swipe seek ${seekSeconds > 0 ? '+' : ''}${seekSeconds}s ${prev.toFixed(1)} -> ${v.currentTime.toFixed(1)}`)
    showHint(hint)
    return
  }

  // Vertical swipe: volume (right side) or brightness hint (left side)
  if (Math.abs(dy) > 60) {
    if (touchStartX.value > window.innerWidth / 2) {
      const delta = dy < 0 ? 0.05 : -0.05
      const prev = v.volume
      v.volume = Math.max(0, Math.min(1, v.volume + delta))
      logInfo('player', `swipe volume ${prev.toFixed(2)} -> ${v.volume.toFixed(2)}`)
      showHint(`音量 ${Math.round(v.volume * 100)}%`)
    } else {
      // 左侧上下滑：浏览器没有系统亮度 API，仅作提示
      showHint(dy < 0 ? '亮度 ↑ (浏览器不支持)' : '亮度 ↓ (浏览器不支持)')
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
      logInfo('player', 'key Escape')
      close()
    } else if (e.key === 'ArrowLeft') {
      e.preventDefault()
      const delta = e.shiftKey ? 5 : 10
      const prev = v.currentTime
      v.currentTime = Math.max(0, v.currentTime - delta)
      logInfo('player', `key ArrowLeft${e.shiftKey ? '+Shift' : ''} seek ${prev.toFixed(1)} -> ${v.currentTime.toFixed(1)}`)
    } else if (e.key === 'ArrowRight') {
      e.preventDefault()
      const delta = e.shiftKey ? 5 : 10
      const prev = v.currentTime
      v.currentTime = Math.min(v.duration || Infinity, v.currentTime + delta)
      logInfo('player', `key ArrowRight${e.shiftKey ? '+Shift' : ''} seek ${prev.toFixed(1)} -> ${v.currentTime.toFixed(1)}`)
    } else if (e.key === 'ArrowUp') {
      e.preventDefault()
      const prev = v.volume
      v.volume = Math.min(1, v.volume + 0.1)
      logInfo('player', `key ArrowUp volume ${prev.toFixed(2)} -> ${v.volume.toFixed(2)}`)
    } else if (e.key === 'ArrowDown') {
      e.preventDefault()
      const prev = v.volume
      v.volume = Math.max(0, v.volume - 0.1)
      logInfo('player', `key ArrowDown volume ${prev.toFixed(2)} -> ${v.volume.toFixed(2)}`)
    } else if (e.key === ' ') {
      e.preventDefault()
      if (v.paused) {
        v.play()
        logInfo('player', 'key Space play')
      } else {
        v.pause()
        logInfo('player', 'key Space pause')
      }
    } else if (e.key === 'f' || e.key === 'F') {
      e.preventDefault()
      logInfo('player', 'key F fullscreen')
      toggleFullscreen()
    } else if (e.key === 'm' || e.key === 'M') {
      e.preventDefault()
      v.muted = !v.muted
      logInfo('player', `key M muted=${v.muted}`)
    } else if (e.key >= '0' && e.key <= '9') {
      e.preventDefault()
      const pct = parseInt(e.key) / 10
      if (v.duration && isFinite(v.duration)) {
        const prev = v.currentTime
        v.currentTime = v.duration * pct
        logInfo('player', `key ${e.key} seek ${prev.toFixed(1)} -> ${v.currentTime.toFixed(1)} (${pct * 100}%)`)
      }
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
