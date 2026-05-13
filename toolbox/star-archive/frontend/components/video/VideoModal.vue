<template>
  <Teleport to="body">
    <div
      v-if="isOpen"
      class="fixed inset-0 z-[100] flex items-center justify-center bg-void/95 backdrop-blur-md"
      @click.self="close"
    >
      <div
        ref="containerRef"
        class="relative w-full h-full sm:w-[90vw] sm:max-w-5xl sm:h-auto sm:aspect-video bg-void sm:rounded-glass-lg overflow-hidden ring-1 ring-glass-border"
        @dblclick="onDblClick"
        @touchstart="onTouchStart"
        @touchend="onTouchEnd"
        @touchmove="onTouchMove"
      >
        <!-- Close button -->
        <button
          class="absolute top-3 right-3 z-20 w-12 h-12 rounded-full glass-strong active:bg-white/10 text-white flex items-center justify-center text-xl transition-colors touch-manipulation"
          @click="close"
        >
          ✕
        </button>

        <!-- Fullscreen toggle -->
        <button
          class="absolute top-3 left-3 z-20 w-12 h-12 rounded-full glass-strong active:bg-white/10 text-white flex items-center justify-center text-lg transition-colors touch-manipulation"
          @click="toggleFullscreen"
        >
          {{ isFullscreen ? '⤓' : '⤢' }}
        </button>

        <!-- Video player -->
        <video
          v-if="!loading && !errorMsg"
          ref="videoRef"
          playsinline
          webkit-playsinline
          x5-playsinline
          x5-video-player-type="h5"
          x5-video-player-fullscreen="false"
          controlsList="nodownload noremoteplayback"
          preload="auto"
          muted
          crossorigin="anonymous"
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
          @pause="onPause"
        />

        <!-- Custom controls overlay -->
        <div
          v-show="!loading && !errorMsg"
          class="absolute inset-x-0 bottom-0 z-20 bg-gradient-to-t from-black/80 via-black/40 to-transparent px-4 pb-4 pt-12 transition-opacity duration-500"
          :class="{ 'opacity-0 pointer-events-none': controlsHidden }"
          @mousemove="showControls"
          @touchstart="showControls"
        >
          <!-- Progress bar -->
          <div
            ref="progressBarRef"
            class="relative h-1.5 bg-white/15 rounded-full cursor-pointer group"
            @click="onProgressClick"
          >
            <!-- Buffered segments -->
            <div
              v-for="(range, i) in bufferedRanges"
              :key="i"
              class="absolute h-full bg-white/20 rounded-full"
              :style="{ left: range.start + '%', width: range.width + '%' }"
            />
            <!-- Played -->
            <div
              class="absolute h-full bg-gradient-to-r from-rose to-violet rounded-full"
              :style="{ width: progressPercent + '%' }"
            />
            <!-- Thumb -->
            <div
              class="absolute top-1/2 -translate-y-1/2 w-3 h-3 bg-white rounded-full shadow opacity-0 group-hover:opacity-100 transition-opacity"
              :style="{ left: 'calc(' + progressPercent + '% - 6px)' }"
            />
          </div>

          <!-- Controls row -->
          <div class="flex items-center justify-between mt-3">
            <div class="flex items-center gap-3">
              <button
                class="w-8 h-8 flex items-center justify-center text-white text-lg touch-manipulation hover:text-rose transition-colors"
                @click="togglePlay"
              >
                {{ isPlaying ? '⏸' : '▶' }}
              </button>
              <span class="text-xs text-white/90 font-mono tabular-nums">
                {{ formatTime(currentTime) }} / {{ formatTime(duration) }}
              </span>
            </div>
            <button
              class="w-8 h-8 flex items-center justify-center text-white text-sm touch-manipulation"
              @click="toggleFullscreen"
            >
              {{ isFullscreen ? '⤓' : '⤢' }}
            </button>
          </div>
        </div>

        <!-- Loading overlay -->
        <div
          v-if="loading"
          class="absolute inset-0 flex flex-col items-center justify-center gap-4 bg-void/80 z-10"
        >
          <div class="w-10 h-10 rounded-full border-4 border-white/10 border-t-rose animate-spin" />
          <p class="text-white text-sm">
            {{ statusText }}
          </p>
        </div>

        <!-- Error overlay -->
        <div
          v-if="errorMsg"
          class="absolute inset-0 flex flex-col items-center justify-center gap-4 bg-void/80 z-10"
        >
          <p class="text-red-400 text-sm">{{ errorMsg }}</p>
        </div>

        <!-- Buffer status overlay (small, bottom-left) -->
        <div
          v-if="buffering && !loading"
          class="absolute bottom-20 left-4 z-20 glass px-3 py-1.5 rounded-glass-sm text-xs text-foreground-muted"
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
import { nextTick } from 'vue'
import { logInfo, logError } from '~/composables/useLogger'

const isOpen = defineModel<boolean>('open', { default: false })
const props = defineProps<{ hash?: string }>()

const videoRef = ref<HTMLVideoElement>()
const containerRef = ref<HTMLDivElement>()
const progressBarRef = ref<HTMLDivElement>()
const { status, loading, error, canplayFired, startPolling, stopPolling, waitForHeadReady, reportSeek, reportProgress, formatSpeed } = useVideoPlayer()

const buffering = ref(false)
const errorMsg = ref('')
const isFullscreen = ref(false)
const retryCount = ref(0)
const MAX_RETRIES = 3

// Custom controls state
const currentTime = ref(0)
const duration = ref(0)
const isPlaying = ref(false)
const controlsHidden = ref(false)
let controlsHideTimer: ReturnType<typeof setTimeout> | null = null

// Seek state: track whether video was playing before seek started
// so we can resume after seeked (instead of relying on isPlaying
// which gets clobbered by the pause event).
const wasPlayingBeforeSeek = ref(false)

const progressPercent = computed(() => {
  if (!duration.value || duration.value === Infinity) return 0
  return (currentTime.value / duration.value) * 100
})

interface BufferedRange { start: number; width: number }
const bufferedRanges = computed<BufferedRange[]>(() => {
  const v = videoRef.value
  if (!v || !v.buffered || !duration.value || duration.value === Infinity) return []
  const ranges: BufferedRange[] = []
  for (let i = 0; i < v.buffered.length; i++) {
    const start = (v.buffered.start(i) / duration.value) * 100
    const end = (v.buffered.end(i) / duration.value) * 100
    ranges.push({ start, width: end - start })
  }
  return ranges
})

function formatTime(sec: number): string {
  if (!sec || !isFinite(sec)) return '0:00'
  const h = Math.floor(sec / 3600)
  const m = Math.floor((sec % 3600) / 60)
  const s = Math.floor(sec % 60)
  if (h > 0) return `${h}:${m.toString().padStart(2, '0')}:${s.toString().padStart(2, '0')}`
  return `${m}:${s.toString().padStart(2, '0')}`
}

function showControls() {
  controlsHidden.value = false
  if (controlsHideTimer) clearTimeout(controlsHideTimer)
  controlsHideTimer = setTimeout(() => {
    if (isPlaying.value && !buffering.value) controlsHidden.value = true
  }, 3000)
}

function onProgressClick(e: MouseEvent) {
  const v = videoRef.value
  const bar = progressBarRef.value
  if (!v || !v.duration || !bar) return
  const rect = bar.getBoundingClientRect()
  const ratio = (e.clientX - rect.left) / rect.width
  const newTime = v.duration * Math.max(0, Math.min(1, ratio))
  v.currentTime = newTime
  currentTime.value = newTime
  // Force controls visible briefly so user sees the seek feedback
  showControls()
}

// Progress persistence
const PROGRESS_KEY = 'claw_video_progress'
const PROGRESS_SAVE_INTERVAL_MS = 5000
const PROGRESS_REPORT_INTERVAL_MS = 10000
let lastProgressSave = 0
let lastProgressReport = 0

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

function formatEta(seconds: number): string {
  if (seconds < 60) return `${Math.ceil(seconds)}s`
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ${Math.ceil(seconds % 60)}s`
  return `${Math.floor(seconds / 3600)}h ${Math.floor((seconds % 3600) / 60)}m`
}

const statusText = computed(() => {
  if (!status.value) return '连接种子...'
  const s = status.value
  if (!s.ready) return '连接种子...'
  const peers = s.peers > 0 ? `${s.peers} peers` : ''
  if (!s.head_ready) {
    const speed = formatSpeed(s.download_rate)
    const pct = s.progress.toFixed(1)
    const buf = s.local_size ? (s.local_size / 1024 / 1024).toFixed(0) + 'MB' : ''
    const total = s.video_size ? (s.video_size / 1024 / 1024 / 1024).toFixed(1) + 'GB' : ''
    const eta = s.download_rate > 0 && s.video_size > s.local_size
      ? formatEta((s.video_size - s.local_size) / s.download_rate)
      : ''
    const parts = ['缓冲中']
    if (peers) parts.push(peers)
    parts.push(speed)
    if (buf) parts.push(`已缓存 ${buf}`)
    if (total) parts.push(`/ ${total}`)
    parts.push(`(${pct}%)`)
    if (eta) parts.push(`ETA ${eta}`)
    return parts.join(' | ')
  }
  if (s.download_rate > 0) {
    const buf = s.local_size ? (s.local_size / 1024 / 1024).toFixed(0) + 'MB' : ''
    const total = s.video_size ? (s.video_size / 1024 / 1024 / 1024).toFixed(1) + 'GB' : ''
    const parts = ['播放中']
    if (peers) parts.push(peers)
    parts.push(formatSpeed(s.download_rate))
    if (buf) parts.push(`已缓存 ${buf}`)
    if (total) parts.push(`/ ${total}`)
    return parts.join(' | ')
  }
  return '准备播放...'
})

// 同时 watch hash 和 isOpen，避免 Vue 响应式时序导致两者不同时更新时漏触发
watch([() => props.hash, isOpen], async ([hash, open]) => {
  if (!hash || !open) return
  // 避免重复加载同一 hash
  if (videoRef.value?.src && videoRef.value.src.includes(hash)) return
  errorMsg.value = ''
  canplayFired.value = false
  buffering.value = true
  retryCount.value = 0
  logInfo('player', `open video hash=${hash.slice(0, 12)}`)

  const ready = await waitForHeadReady(hash)
  if (!ready) {
    errorMsg.value = error.value || '加载失败'
    logError('player', `load failed hash=${hash.slice(0, 12)}: ${errorMsg.value}`)
    return
  }

  // iOS Safari: element must be fully mounted and visible before src is set.
  // nextTick() alone is not enough; use setTimeout to defer past layout.
  await nextTick()
  setTimeout(() => {
    const v = videoRef.value
    if (v) {
      v.src = streamUrl.value
      v.load()
      logInfo('player', `src set hash=${hash.slice(0, 12)} readyState=${v.readyState} networkState=${v.networkState}`)
    } else {
      logError('player', `videoRef missing after nextTick+setTimeout hash=${hash.slice(0, 12)}`)
    }
  }, 200)

  startPolling(hash)
})

watch(isOpen, (open) => {
  if (!open) {
    saveProgress()
    stopPolling()
    if (controlsHideTimer) clearTimeout(controlsHideTimer)
    controlsHidden.value = false
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
    currentTime.value = 0
    duration.value = 0
    isPlaying.value = false
  }
})

function close() {
  logInfo('player', 'close video')
  isOpen.value = false
}

function onLoadedMetadata() {
  const v = videoRef.value
  if (v) {
    duration.value = v.duration || 0
  }
  cleanupOldProgress()
  restoreProgress()
}

function onCanplay() {
  canplayFired.value = true
  buffering.value = false
  retryCount.value = 0
  const v = videoRef.value
  logInfo('player', 'canplay', {
    currentTime: v?.currentTime ?? 0,
    bufferedRanges: v?.buffered?.length ?? 0,
  })
  // Auto-play on first canplay, but do not race with a pending togglePlay().
  // If the user already clicked play (isPlaying true or play pending), skip.
  if (!isPlaying.value && v?.paused) {
    v?.play().then(() => {
      isPlaying.value = true
    }).catch((err: any) => {
      logError('player', `canplay play() rejected: ${err?.name || err?.message || err}`)
    })
  }
}

function onTimeUpdate() {
  const v = videoRef.value
  if (v) {
    currentTime.value = v.currentTime
    duration.value = v.duration || 0
  }
  const now = Date.now()
  if (now - lastProgressSave > PROGRESS_SAVE_INTERVAL_MS) {
    saveProgress()
    lastProgressSave = now
  }
  if (now - lastProgressReport > PROGRESS_REPORT_INTERVAL_MS) {
    if (v && v.duration && isFinite(v.duration)) {
      reportProgress(props.hash || '', v.currentTime, v.duration)
    }
    lastProgressReport = now
  }
}

function onEnded() {
  isPlaying.value = false
  logInfo('player', 'ended')
  clearProgress()
}

function onWaiting() {
  buffering.value = true
  isPlaying.value = false
  const v = videoRef.value
  logInfo('player', 'waiting', {
    currentTime: v?.currentTime ?? 0,
    readyState: v?.readyState ?? -1,
  })
}
function onPlaying() {
  buffering.value = false
  isPlaying.value = true
  logInfo('player', 'playing')
}
function onSeeking() {
  buffering.value = true
  const v = videoRef.value
  // Snapshot play state BEFORE browser pauses internally.
  // Do NOT call pause() manually — it fires a 'pause' event that
  // clobbers isPlaying and breaks the seeked-auto-resume logic.
  wasPlayingBeforeSeek.value = isPlaying.value
  logInfo('player', 'seeking', {
    currentTime: v?.currentTime ?? 0,
    duration: v?.duration ?? 0,
    wasPlaying: wasPlayingBeforeSeek.value,
  })
}
function onSeeked() {
  buffering.value = false
  const v = videoRef.value
  if (v) {
    currentTime.value = v.currentTime
    duration.value = v.duration || 0
  }
  logInfo('player', 'seeked', {
    currentTime: v?.currentTime ?? 0,
    duration: v?.duration ?? 0,
    wasPlaying: wasPlayingBeforeSeek.value,
  })
  if (v && v.duration && isFinite(v.duration) && props.hash) {
    reportSeek(props.hash, v.currentTime, v.duration)
  }
  // Resume playback only if we were actually playing before the seek.
  if (wasPlayingBeforeSeek.value) {
    v?.play().then(() => {
      isPlaying.value = true
    }).catch(() => {})
  }
}

function onPause() {
  isPlaying.value = false
  logInfo('player', 'pause')
}

function onStalled() {
  buffering.value = true
  isPlaying.value = false
  const v = videoRef.value
  logInfo('player', 'stalled', {
    currentTime: v?.currentTime ?? 0,
    readyState: v?.readyState ?? -1,
    networkState: v?.networkState ?? -1,
  })
}

function onAbort() {
  logInfo('player', 'abort')
  buffering.value = false
}

function onError() {
  isPlaying.value = false
  const v = videoRef.value
  const code = v?.error?.code ?? 0
  const message = v?.error?.message ?? 'unknown'
  const networkState = v?.networkState ?? -1
  const readyState = v?.readyState ?? -1
  const currentSrc = v?.currentSrc ?? ''
  const buffered = v?.buffered?.length ?? 0
  logError('player', `video error code=${code} msg=${message}`, {
    networkState,
    readyState,
    currentSrc: currentSrc.slice(-60),
    bufferedRanges: buffered,
    canplayFired: canplayFired.value,
    retryCount: retryCount.value,
    hash: props.hash?.slice(0, 12),
  })

  // code=4 (MEDIA_ERR_SRC_NOT_SUPPORTED) — give it one retry before giving up.
  // Safari may temporarily report code=4 during initial load; a reload often fixes it.
  if (code === 4 && retryCount.value < MAX_RETRIES) {
    retryCount.value++
    logInfo('player', `code=4 retry ${retryCount.value}/${MAX_RETRIES}`)
    errorMsg.value = `加载中 (${retryCount.value}/${MAX_RETRIES})...`
    const src = streamUrl.value
    setTimeout(() => {
      if (videoRef.value) {
        videoRef.value.src = src
        videoRef.value.load()
      }
    }, 1000)
    return
  }

  if (code === 4) {
    errorMsg.value = '播放失败，文件格式不支持或数据损坏'
    stopPolling()
    return
  }

  if (retryCount.value < MAX_RETRIES) {
    retryCount.value++
    logInfo('player', `retry ${retryCount.value}/${MAX_RETRIES}`)
    errorMsg.value = `加载中 (${retryCount.value}/${MAX_RETRIES})...`
    const src = v?.src || streamUrl.value
    setTimeout(() => {
      if (videoRef.value) {
        videoRef.value.src = src
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
    v.play().then(() => {
      isPlaying.value = true
      logInfo('player', 'toggle play success')
    }).catch((err: any) => {
      isPlaying.value = false
      logError('player', `toggle play rejected: ${err?.name || err?.message || err}`)
    })
  } else {
    v.pause()
    isPlaying.value = false
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
