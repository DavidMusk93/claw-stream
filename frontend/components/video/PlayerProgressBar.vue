<template>
  <!-- Progress bar — hit target taller than the visual bar (44px-ish touch zone).
       Pointer Events drive both click-seek and drag-scrub; touch handlers are
       stopped so the container's swipe/dblclick gestures don't fire while
       scrubbing the bar. -->
  <div
    ref="barRef"
    class="relative py-2.5 -my-2.5 cursor-pointer group touch-none select-none"
    @pointerdown="onPointerDown"
    @pointermove="onPointerMove"
    @pointerup="onPointerUp"
    @pointercancel="onPointerCancel"
    @pointerleave="onPointerLeave"
    @click.stop
    @dblclick.stop
    @touchstart.stop
    @touchmove.stop
    @touchend.stop
  >
    <!-- Hover / scrub tooltip -->
    <div
      v-if="bubblePercent !== null"
      class="absolute bottom-full mb-2 -translate-x-1/2 pointer-events-none z-10"
      :style="{ left: bubbleLeft + '%' }"
    >
      <div class="flex items-center gap-1.5 bg-black/80 backdrop-blur-md border border-white/10 rounded-full px-2.5 py-1 text-[11px] text-white/90 font-mono tabular-nums whitespace-nowrap">
        <span
          v-if="bubbleState !== null"
          class="w-1.5 h-1.5 rounded-full"
          :class="stateDotClass(bubbleState)"
        />
        <span>{{ formatTime(bubbleTime) }}</span>
        <span v-if="bubbleState !== null" class="text-white/50">{{ stateLabel(bubbleState) }}</span>
      </div>
    </div>

    <div class="relative h-1.5 sm:h-2 bg-white/15 rounded-full">
      <!-- Download-state map (server piece bitmap, live via SSE) -->
      <template v-if="segments.length">
        <div
          v-for="(seg, i) in segments"
          :key="i"
          class="absolute h-full"
          :class="segmentClass(seg[2])"
          :style="{ left: seg[0] + '%', width: Math.max(0, seg[1] - seg[0]) + '%' }"
        />
      </template>
      <!-- Fallback: browser buffer ranges until the first status arrives -->
      <template v-else>
        <div
          v-for="(range, i) in buffered"
          :key="i"
          class="absolute h-full bg-white/25 rounded-full"
          :style="{ left: range.start + '%', width: range.width + '%' }"
        />
      </template>

      <!-- Playhead fill -->
      <div
        class="absolute h-full bg-[#ff375f] rounded-full"
        :style="{ width: displayPercent + '%' }"
      />
      <!-- Thumb -->
      <div
        class="absolute top-1/2 -translate-y-1/2 w-4 h-4 bg-white rounded-full shadow transition-opacity"
        :class="scrubbing ? 'opacity-100' : 'opacity-0 group-hover:opacity-100'"
        :style="{ left: 'calc(' + displayPercent + '% - 8px)' }"
      />
    </div>
  </div>
</template>

<script setup lang="ts">
const props = defineProps<{
  duration: number
  currentTime: number
  /** [start_pct, end_pct, state][] — state: 0=missing 1=downloading 2=cached 3=corrupt */
  segments: [number, number, number][]
  buffered: { start: number; width: number }[]
}>()

const emit = defineEmits<{
  seek: [time: number]
  scrubbing: [active: boolean]
}>()

const barRef = ref<HTMLDivElement>()
const scrubbing = ref(false)
const scrubPercent = ref(0)
const hoverPercent = ref<number | null>(null)

const progressPercent = computed(() => {
  if (!props.duration || props.duration === Infinity) return 0
  return (props.currentTime / props.duration) * 100
})

const displayPercent = computed(() =>
  scrubbing.value ? scrubPercent.value : progressPercent.value,
)

const bubblePercent = computed(() =>
  scrubbing.value ? scrubPercent.value : hoverPercent.value,
)

// Keep the bubble inside the bar so it never clips at the edges.
const bubbleLeft = computed(() => {
  const p = bubblePercent.value ?? 0
  return Math.max(4, Math.min(96, p))
})

const bubbleTime = computed(() =>
  props.duration && isFinite(props.duration)
    ? (props.duration * (bubblePercent.value ?? 0)) / 100
    : 0,
)

const bubbleState = computed<number | null>(() => {
  const p = bubblePercent.value
  if (p === null || !props.segments.length) return null
  return stateAt(p)
})

function stateAt(pct: number): number | null {
  for (const seg of props.segments) {
    if (pct >= seg[0] && pct < seg[1]) return seg[2]
  }
  return props.segments.length ? props.segments[props.segments.length - 1][2] : null
}

function segmentClass(state: number): string {
  switch (state) {
    case 2: return 'bg-white/40'
    case 1: return 'bg-amber-400/70 animate-pulse'
    case 3: return 'bg-red-500/60'
    default: return ''
  }
}

function stateDotClass(state: number): string {
  switch (state) {
    case 2: return 'bg-white/70'
    case 1: return 'bg-amber-400'
    case 3: return 'bg-red-500'
    default: return 'bg-white/25'
  }
}

function stateLabel(state: number): string {
  switch (state) {
    case 2: return 'Cached'
    case 1: return 'Downloading'
    case 3: return 'Corrupt'
    default: return 'Not cached'
  }
}

function formatTime(sec: number): string {
  if (!sec || !isFinite(sec)) return '0:00'
  const h = Math.floor(sec / 3600)
  const m = Math.floor((sec % 3600) / 60)
  const s = Math.floor(sec % 60)
  if (h > 0) return `${h}:${m.toString().padStart(2, '0')}:${s.toString().padStart(2, '0')}`
  return `${m}:${s.toString().padStart(2, '0')}`
}

function percentFromEvent(e: PointerEvent): number {
  const bar = barRef.value
  if (!bar) return 0
  const rect = bar.getBoundingClientRect()
  return Math.max(0, Math.min(100, ((e.clientX - rect.left) / rect.width) * 100))
}

function onPointerDown(e: PointerEvent) {
  if (!props.duration || props.duration === Infinity) return
  barRef.value?.setPointerCapture(e.pointerId)
  scrubbing.value = true
  scrubPercent.value = percentFromEvent(e)
  emit('scrubbing', true)
}

function onPointerMove(e: PointerEvent) {
  if (scrubbing.value) {
    scrubPercent.value = percentFromEvent(e)
  } else if (e.pointerType === 'mouse') {
    hoverPercent.value = percentFromEvent(e)
  }
}

function finishScrub(e: PointerEvent, commit: boolean) {
  if (!scrubbing.value) return
  scrubPercent.value = percentFromEvent(e)
  scrubbing.value = false
  emit('scrubbing', false)
  if (commit && props.duration && isFinite(props.duration)) {
    emit('seek', (props.duration * scrubPercent.value) / 100)
  }
}

function onPointerUp(e: PointerEvent) {
  finishScrub(e, true)
}

function onPointerCancel(e: PointerEvent) {
  finishScrub(e, false)
}

function onPointerLeave() {
  if (!scrubbing.value) hoverPercent.value = null
}
</script>
