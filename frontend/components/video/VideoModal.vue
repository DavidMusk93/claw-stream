<template>
  <Teleport to="body">
    <div
      v-if="isOpen"
      class="fixed inset-0 z-[100] flex items-center justify-center bg-black/92 backdrop-blur-xl"
      @click.self="close"
    >
      <div
        ref="containerRef"
        class="relative w-full h-full sm:w-[92vw] sm:max-w-6xl sm:h-auto sm:aspect-video bg-black sm:rounded-2xl overflow-hidden shadow-2xl ring-1 ring-white/10"
        @dblclick="onDblClick"
        @touchstart="onTouchStart"
        @touchend="onTouchEnd"
        @touchmove="onTouchMove"
        @mousemove="showControls"
      >
        <!-- Top bar -->
        <div
          class="absolute top-0 inset-x-0 z-30 flex items-center justify-between p-3 sm:p-4 bg-gradient-to-b from-black/70 to-transparent transition-opacity duration-300"
          :class="{ 'opacity-0 pointer-events-none': controlsHidden }"
        >
          <button
            class="w-10 h-10 rounded-full bg-white/10 backdrop-blur-md text-white flex items-center justify-center transition hover:bg-white/20 active:scale-95"
            @click="toggleFullscreen"
          >
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
              <path v-if="isFullscreen" d="M8 3v3a2 2 0 0 1-2 2H3m18 0h-3a2 2 0 0 1-2-2V3M3 16h3a2 2 0 0 1 2 2v3m13-3h-3a2 2 0 0 0-2 2v3"/>
              <path v-else d="M8 3H5a2 2 0 0 0-2 2v3m18 0V5a2 2 0 0 0-2-2h-3m0 18h3a2 2 0 0 0 2-2v-3M3 16v3a2 2 0 0 0 2 2h3"/>
            </svg>
          </button>

          <button
            class="w-10 h-10 rounded-full bg-white/10 backdrop-blur-md text-white flex items-center justify-center transition hover:bg-white/20 active:scale-95"
            @click="close"
          >
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
              <line x1="18" y1="6" x2="6" y2="18"/>
              <line x1="6" y1="6" x2="18" y2="18"/>
            </svg>
          </button>
        </div>

        <!-- Video -->
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
          @click="onVideoClick"
        />

        <!-- Custom controls -->
        <div
          v-show="!loading && !errorMsg"
          class="absolute inset-x-0 bottom-0 z-30 bg-gradient-to-t from-black/85 via-black/50 to-transparent px-4 sm:px-5 pb-5 pt-14 transition-opacity duration-300"
          :class="{ 'opacity-0 pointer-events-none': controlsHidden }"
          @mousemove="showControls"
          @touchstart="showControls"
        >
          <!-- Progress bar — hit target taller than the visual bar (44px-ish touch zone) -->
          <div
            ref="progressBarRef"
            class="relative py-2.5 -my-2.5 cursor-pointer group"
            @click="onProgressClick"
          >
            <div class="relative h-1.5 sm:h-2 bg-white/15 rounded-full">
              <div
                v-for="(range, i) in bufferedRanges"
                :key="i"
                class="absolute h-full bg-white/25 rounded-full"
                :style="{ left: range.start + '%', width: range.width + '%' }"
              />
              <div
                class="absolute h-full bg-[#ff375f] rounded-full"
                :style="{ width: progressPercent + '%' }"
              />
              <div
                class="absolute top-1/2 -translate-y-1/2 w-4 h-4 bg-white rounded-full shadow opacity-0 group-hover:opacity-100 transition-opacity"
                :style="{ left: 'calc(' + progressPercent + '% - 8px)' }"
              />
            </div>
          </div>

          <!-- Controls row -->
          <div class="flex items-center justify-between mt-3">
            <div class="flex items-center gap-3">
              <button
                class="w-10 h-10 rounded-full bg-white/10 text-white flex items-center justify-center transition hover:bg-white/20 active:scale-[0.97]"
                @click="togglePlay"
              >
                <svg v-if="!isPlaying" width="18" height="18" viewBox="0 0 24 24" fill="currentColor">
                  <path d="M8 5v14l11-7z"/>
                </svg>
                <svg v-else width="18" height="18" viewBox="0 0 24 24" fill="currentColor">
                  <rect x="6" y="4" width="4" height="16"/>
                  <rect x="14" y="4" width="4" height="16"/>
                </svg>
              </button>

              <button
                class="w-10 h-10 rounded-full bg-white/10 text-white flex items-center justify-center transition hover:bg-white/20 active:scale-[0.97]"
                @click="toggleMute"
              >
                <svg v-if="isMuted" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                  <polygon points="11 5 6 9 2 9 2 15 6 15 11 19 11 5"/>
                  <line x1="23" y1="9" x2="17" y2="15"/>
                  <line x1="17" y1="9" x2="23" y2="15"/>
                </svg>
                <svg v-else width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                  <polygon points="11 5 6 9 2 9 2 15 6 15 11 19 11 5"/>
                  <path d="M15.54 8.46a5 5 0 0 1 0 7.07"/>
                  <path d="M19.07 4.93a10 10 0 0 1 0 14.14"/>
                </svg>
              </button>

              <span class="text-[13px] text-white/90 font-mono tabular-nums">
                {{ formatTime(currentTime) }} / {{ formatTime(duration) }}
              </span>
            </div>

            <button
              class="w-10 h-10 rounded-full bg-white/10 text-white flex items-center justify-center transition hover:bg-white/20 active:scale-[0.97]"
              @click="toggleFullscreen"
            >
              <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                <path v-if="isFullscreen" d="M8 3v3a2 2 0 0 1-2 2H3m18 0h-3a2 2 0 0 1-2-2V3M3 16h3a2 2 0 0 1 2 2v3m13-3h-3a2 2 0 0 0-2 2v3"/>
                <path v-else d="M8 3H5a2 2 0 0 0-2 2v3m18 0V5a2 2 0 0 0-2-2h-3m0 18h3a2 2 0 0 0 2-2v-3M3 16v3a2 2 0 0 0 2 2h3"/>
              </svg>
            </button>
          </div>
        </div>

        <!-- Loading overlay -->
        <div
          v-if="loading"
          class="absolute inset-0 flex flex-col items-center justify-center gap-5 bg-black/80 z-20"
        >
          <div class="w-12 h-12 rounded-full border-4 border-white/10 border-t-[#ff375f] animate-spin" />

          <div class="w-80 max-w-[85vw]">
            <div class="h-2 bg-white/10 rounded-full overflow-hidden">
              <div
                class="h-full bg-[#ff375f] rounded-full transition-all duration-500"
                :style="{ width: Math.min(status?.progress || 0, 100) + '%' }"
              />
            </div>
            <div class="mt-2 flex justify-between text-xs text-white/60">
              <span>
                {{ status?.state?.includes('checking') ? 'Verifying' : 'Downloading' }}
                {{ (status?.progress || 0).toFixed(1) }}%
              </span>
              <span v-if="status?.video_size">{{ (status.video_size / 1024 / 1024 / 1024).toFixed(1) }} GB</span>
            </div>
          </div>

          <p class="text-white text-lg font-semibold">
            {{ statusText }}
          </p>
          <p class="text-white/60 text-sm">
            {{ cacheRatio }}
          </p>
          <p class="text-white/40 text-xs">
            {{ detailStatus }}
          </p>
        </div>

        <!-- Error overlay -->
        <div
          v-if="errorMsg"
          class="absolute inset-0 flex flex-col items-center justify-center gap-4 bg-black/85 z-20 px-6 text-center"
        >
          <div class="w-14 h-14 rounded-full bg-white/10 flex items-center justify-center text-[#ff453a]">
            <svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
              <circle cx="12" cy="12" r="10"/>
              <line x1="12" y1="8" x2="12" y2="12"/>
              <line x1="12" y1="16" x2="12.01" y2="16"/>
            </svg>
          </div>
          <p class="text-white/90 text-base font-medium max-w-md">{{ errorMsg }}</p>
          <button
            v-if="retryCount < MAX_RETRIES"
            class="px-5 py-2 rounded-full bg-white text-black text-sm font-semibold transition hover:bg-white/90 active:scale-95"
            @click="doRetry"
          >
            Retry
          </button>
        </div>

        <!-- Buffer status overlay -->
        <div
          v-if="buffering && !loading"
          class="absolute bottom-24 left-4 z-30 bg-black/50 backdrop-blur-md border border-white/10 px-3 py-1.5 rounded-full text-xs text-white/80 flex items-center gap-2"
        >
          <div class="w-3 h-3 rounded-full border-2 border-white/20 border-t-white animate-spin" />
          {{ statusText }}
        </div>

        <!-- Gesture hint -->
        <div
          v-if="showGestureHint"
          class="absolute inset-0 flex items-center justify-center z-40 pointer-events-none"
        >
          <div class="bg-black/60 text-white px-5 py-2 rounded-full text-sm font-medium animate-fade-out">
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
const { status, loading, error, canplayFired, startPolling, stopPolling, waitForHeadReady, reportSeek, reportProgress, reportPause, reportResume, formatSpeed } = useVideoPlayer()

const buffering = ref(false)
const errorMsg = ref('')
const isFullscreen = ref(false)
const isMuted = ref(false)
const retryCount = ref(0)
const MAX_RETRIES = 3

const currentTime = ref(0)
const duration = ref(0)
const isPlaying = ref(false)
const controlsHidden = ref(false)
let controlsHideTimer: ReturnType<typeof setTimeout> | null = null
const activeTimers = new Set<ReturnType<typeof setTimeout>>()

function safeSetTimeout(fn: () => void, delay: number) {
  const id = setTimeout(() => {
    activeTimers.delete(id)
    fn()
  }, delay)
  activeTimers.add(id)
  return id
}

function clearAllTimers() {
  activeTimers.forEach(id => clearTimeout(id))
  activeTimers.clear()
}

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
  controlsHideTimer = safeSetTimeout(() => {
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
  showControls()
}

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
    // ignore
  }
}

function saveProgress() {
  const v = videoRef.value
  const hash = props.hash
  if (!v || !hash || !v.duration || v.duration === Infinity) return
  if (v.currentTime < 3) return
  if (v.currentTime / v.duration > 0.95) return

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
  const cutoff = Date.now() - 30 * 24 * 60 * 60 * 1000
  let changed = false
  for (const key of Object.keys(map)) {
    if (map[key].updatedAt < cutoff) {
      delete map[key]
      changed = true
    }
  }
  if (changed) saveProgressMap(map)
}

const touchStartX = ref(0)
const touchStartY = ref(0)
const touchStartTime = ref(0)
const showGestureHint = ref(false)
const gestureHintText = ref('')

const streamUrl = computed(() => props.hash ? `/stream/${props.hash}` : '')

const cacheRatio = computed(() => {
  if (!status.value) return ''
  const s = status.value
  if (!s.video_size) return ''
  const pct = (s.local_size / s.video_size * 100).toFixed(1)
  const mb = (s.local_size / 1024 / 1024).toFixed(0)
  const gb = (s.video_size / 1024 / 1024 / 1024).toFixed(1)
  return `Cached ${mb}MB / ${gb}GB (${pct}%)`
})

const detailStatus = computed(() => {
  if (!status.value) return ''
  const s = status.value
  const parts: string[] = []
  if (s.peers > 0) parts.push(`${s.peers} peers`)
  if (s.download_rate > 0) parts.push(formatSpeed(s.download_rate))
  if (s.verified_pieces > 0) parts.push(`Verified ${s.verified_pieces} pcs`)
  if (!s.ready || !s.head_ready) {
    const eta = s.download_rate > 0 && s.video_size > s.local_size
      ? formatEta((s.video_size - s.local_size) / s.download_rate)
      : ''
    if (eta) parts.push(`ETA ${eta}`)
  }
  return parts.join(' | ')
})

function formatEta(seconds: number): string {
  if (seconds < 60) return `${Math.ceil(seconds)}s`
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ${Math.ceil(seconds % 60)}s`
  return `${Math.floor(seconds / 3600)}h ${Math.floor((seconds % 3600) / 60)}m`
}

const statusText = computed(() => {
  if (!status.value) return 'Connecting...'
  const s = status.value

  const stateMap: Record<string, string> = {
    checking_files: 'Verifying files',
    checking_resume_data: 'Verifying data',
    downloading_metadata: 'Fetching metadata',
    downloading: 'Downloading',
    finished: 'Finished',
    seeding: 'Seeding',
    allocating: 'Allocating',
  }
  const stateText = stateMap[s.state] || s.state || 'Connecting'

  const peers = s.peers > 0 ? `${s.peers} peers` : ''
  const speed = formatSpeed(s.download_rate)
  const verified = s.verified_pieces ? `${s.verified_pieces} pcs` : ''

  if (!s.ready) {
    const parts = [stateText]
    if (peers) parts.push(peers)
    if (s.download_rate > 0) parts.push(speed)
    if (verified && s.state.includes('checking')) parts.push(`Verified ${verified}`)
    return parts.join(' | ')
  }

  if (!s.head_ready) {
    const parts = ['Buffering']
    if (peers) parts.push(peers)
    parts.push(speed)
    const eta = s.download_rate > 0 && s.video_size > s.local_size
      ? formatEta((s.video_size - s.local_size) / s.download_rate)
      : ''
    if (eta) parts.push(`ETA ${eta}`)
    return parts.join(' | ')
  }

  if (s.download_rate > 0) {
    const parts = ['Playing']
    if (peers) parts.push(peers)
    parts.push(speed)
    return parts.join(' | ')
  }

  if (s.progress < 99.9 && (s.state === 'finished' || s.state === 'seeding')) {
    const vp = s.verified_pieces
    const parts = [`Progress ${s.progress.toFixed(1)}%`]
    if (vp) parts.push(`${vp} pcs verified`)
    return parts.join(' | ')
  }

  return 'Ready | Waiting for data'
})

watch([() => props.hash, isOpen], async ([hash, open]) => {
  if (!hash || !open) return
  if (videoRef.value?.src && videoRef.value.src.includes(hash)) return
  errorMsg.value = ''
  canplayFired.value = false
  buffering.value = true
  retryCount.value = 0
  logInfo('player', `open video hash=${hash.slice(0, 12)}`)

  const ready = await waitForHeadReady(hash)
  if (!ready) {
    errorMsg.value = error.value || 'Load failed'
    logError('player', `load failed hash=${hash.slice(0, 12)}: ${errorMsg.value}`)
    return
  }

  await nextTick()
  safeSetTimeout(() => {
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
    clearAllTimers()
    controlsHidden.value = false
    if (props.hash) {
      reportPause(props.hash)
    }
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

function doRetry() {
  errorMsg.value = ''
  retryCount.value = 0
  const v = videoRef.value
  if (v) {
    v.src = streamUrl.value
    v.load()
  }
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
  if (!isPlaying.value && v?.paused) {
    v?.play().then(() => {
      isPlaying.value = true
      if (props.hash && v.duration && isFinite(v.duration)) {
        reportResume(props.hash, v.currentTime, v.duration)
      }
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
  const v = videoRef.value
  if (props.hash && v && v.duration && isFinite(v.duration)) {
    reportResume(props.hash, v.currentTime, v.duration)
  }
}
function onSeeking() {
  buffering.value = true
  const v = videoRef.value
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
  if (wasPlayingBeforeSeek.value) {
    v?.play().then(() => {
      isPlaying.value = true
      if (props.hash && v && v.duration && isFinite(v.duration)) {
        reportResume(props.hash, v.currentTime, v.duration)
      }
    }).catch(() => {})
  }
}

function onPause() {
  isPlaying.value = false
  logInfo('player', 'pause')
  if (props.hash) {
    reportPause(props.hash)
  }
}

function onVideoClick() {
  showControls()
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

  if (code === 4 && retryCount.value < MAX_RETRIES) {
    retryCount.value++
    logInfo('player', `code=4 retry ${retryCount.value}/${MAX_RETRIES}`)
    errorMsg.value = `Loading (${retryCount.value}/${MAX_RETRIES})...`
    const src = streamUrl.value
    safeSetTimeout(() => {
      if (videoRef.value) {
        videoRef.value.src = src
        videoRef.value.load()
      }
    }, 1000)
    return
  }

  if (code === 4) {
    errorMsg.value = 'Playback failed, unsupported format or corrupted data'
    stopPolling()
    return
  }

  if (retryCount.value < MAX_RETRIES) {
    retryCount.value++
    logInfo('player', `retry ${retryCount.value}/${MAX_RETRIES}`)
    errorMsg.value = `Loading (${retryCount.value}/${MAX_RETRIES})...`
    const src = v?.src || streamUrl.value
    safeSetTimeout(() => {
      if (videoRef.value) {
        videoRef.value.src = src
        videoRef.value.load()
      }
    }, 1000)
    return
  }

  errorMsg.value = 'Playback failed, file may be incomplete'
  stopPolling()
}

function togglePlay() {
  const v = videoRef.value
  if (!v) return
  if (v.paused) {
    // Update UI immediately so the button feels synchronous.
    isPlaying.value = true
    v.play().then(() => {
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

function toggleMute() {
  const v = videoRef.value
  if (!v) return
  v.muted = !v.muted
  isMuted.value = v.muted
  logInfo('player', `toggleMute muted=${v.muted}`)
}

function tryContainerFullscreen(el: HTMLElement): boolean {
  const methods = [
    el.requestFullscreen,
    (el as any).webkitRequestFullscreen,
    (el as any).msRequestFullscreen,
  ]
  for (const m of methods) {
    if (m) {
      m.call(el)
        .then(() => { isFullscreen.value = true })
        .catch((err: any) => {
          logError('player', `container fullscreen failed: ${err?.message || err}`)
          tryVideoFullscreen()
        })
      return true
    }
  }
  return false
}

function tryVideoFullscreen(): boolean {
  const v = videoRef.value as any
  if (!v) return false
  const methods = [
    v.requestFullscreen,
    v.webkitRequestFullscreen,
    v.webkitEnterFullScreen,
    v.msRequestFullscreen,
  ]
  for (const m of methods) {
    if (m) {
      m.call(v)
        .then(() => { isFullscreen.value = true })
        .catch((err: any) => {
          logError('player', `video fullscreen failed: ${err?.message || err}`)
        })
      return true
    }
  }
  return false
}

function enterFullscreen(el: HTMLElement) {
  if (!tryContainerFullscreen(el)) {
    tryVideoFullscreen()
  }
}

function exitFullscreen() {
  const doc = document as any
  const methods = [
    document.exitFullscreen,
    doc.webkitExitFullscreen,
    doc.webkitCancelFullScreen,
    doc.msExitFullscreen,
  ]
  for (const m of methods) {
    if (m) {
      m.call(document)
        .then(() => { isFullscreen.value = false })
        .catch((err: any) => {
          logError('player', `exit fullscreen failed: ${err?.message || err}`)
        })
      return
    }
  }
}

onMounted(() => {
  const handler = () => {
    isFullscreen.value = !!(document.fullscreenElement || (document as any).webkitFullscreenElement)
  }
  document.addEventListener('fullscreenchange', handler)
  document.addEventListener('webkitfullscreenchange', handler)

  // iPhone uses video-element fullscreen, not the Fullscreen API.
  const v = videoRef.value as any
  const onWebkitEnter = () => { isFullscreen.value = true }
  const onWebkitExit = () => { isFullscreen.value = false }
  if (v) {
    v.addEventListener('webkitbeginfullscreen', onWebkitEnter)
    v.addEventListener('webkitendfullscreen', onWebkitExit)
  }

  onUnmounted(() => {
    document.removeEventListener('fullscreenchange', handler)
    document.removeEventListener('webkitfullscreenchange', handler)
    if (v) {
      v.removeEventListener('webkitbeginfullscreen', onWebkitEnter)
      v.removeEventListener('webkitendfullscreen', onWebkitExit)
    }
    clearAllTimers()
  })
})

function onTouchStart(e: TouchEvent) {
  const t = e.touches[0]
  touchStartX.value = t.clientX
  touchStartY.value = t.clientY
  touchStartTime.value = Date.now()
}

function onTouchMove(e: TouchEvent) {
  if (e.touches.length !== 1) return
  if (!controlsHidden.value && videoRef.value) {
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
    const prev = v.currentTime
    v.currentTime = Math.max(0, v.currentTime - 10)
    logInfo('player', `dblclick left seek ${prev.toFixed(1)} -> ${v.currentTime.toFixed(1)}`)
    showHint('Rewind 10s')
  } else if (x > width * 2 / 3) {
    const prev = v.currentTime
    v.currentTime = Math.min(v.duration || Infinity, v.currentTime + 10)
    logInfo('player', `dblclick right seek ${prev.toFixed(1)} -> ${v.currentTime.toFixed(1)}`)
    showHint('Forward 10s')
  } else {
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

  if (Math.abs(dx) < 10 && Math.abs(dy) < 10 && dt < 300) {
    togglePlay()
    return
  }

  if (Math.abs(dx) > Math.abs(dy) && Math.abs(dx) > 40) {
    const SWIPE_PX_PER_SECOND = 3
    const seekSeconds = Math.round(dx * SWIPE_PX_PER_SECOND)
    const prev = v.currentTime
    v.currentTime = Math.max(0, Math.min(v.duration || Infinity, v.currentTime + seekSeconds))
    const absSec = Math.abs(seekSeconds)
    let hint = ''
    if (absSec >= 60) {
      hint = `${seekSeconds > 0 ? 'Forward' : 'Rewind'} ${Math.floor(absSec / 60)}m ${absSec % 60}s`
    } else {
      hint = `${seekSeconds > 0 ? 'Forward' : 'Rewind'} ${absSec}s`
    }
    logInfo('player', `swipe seek ${seekSeconds > 0 ? '+' : ''}${seekSeconds}s ${prev.toFixed(1)} -> ${v.currentTime.toFixed(1)}`)
    showHint(hint)
    return
  }

  if (Math.abs(dy) > 60) {
    if (touchStartX.value > window.innerWidth / 2) {
      const delta = dy < 0 ? 0.05 : -0.05
      const prev = v.volume
      v.volume = Math.max(0, Math.min(1, v.volume + delta))
      logInfo('player', `swipe volume ${prev.toFixed(2)} -> ${v.volume.toFixed(2)}`)
      showHint(`Volume ${Math.round(v.volume * 100)}%`)
    } else {
      showHint(dy < 0 ? 'Brightness ↑ (not supported)' : 'Brightness ↓ (not supported)')
    }
  }
}

function showHint(text: string) {
  gestureHintText.value = text
  showGestureHint.value = true
  safeSetTimeout(() => { showGestureHint.value = false }, 800)
}

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
      togglePlay()
    } else if (e.key === 'f' || e.key === 'F') {
      e.preventDefault()
      logInfo('player', 'key F fullscreen')
      toggleFullscreen()
    } else if (e.key === 'm' || e.key === 'M') {
      e.preventDefault()
      toggleMute()
    } else if (e.key === 'c' || e.key === 'C') {
      e.preventDefault()
      controlsHidden.value = !controlsHidden.value
      logInfo('player', `key C controlsHidden=${controlsHidden.value}`)
      if (!controlsHidden.value && controlsHideTimer) clearTimeout(controlsHideTimer)
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
.animate-fade-out {
  animation: fade-out 0.8s ease-out forwards;
}

@keyframes fade-out {
  0% { opacity: 1; }
  70% { opacity: 1; }
  100% { opacity: 0; }
}
</style>
