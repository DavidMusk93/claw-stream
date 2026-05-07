<template>
  <div class="group shrink-0 w-[280px]">
    <!-- 封面：大图悬浮，大圆角 -->
    <div
      class="relative rounded-2xl overflow-hidden cursor-pointer transition-all duration-500 ease-out group-hover:scale-[1.03]"
      :class="title.cover_url ? 'shadow-[0_0_0_1px_rgba(255,255,255,0.06)]' : 'bg-neutral-900/50'"
      @click="title.magnet && $emit('play', title.magnet)"
    >
      <img
        v-if="title.cover_url"
        :src="title.cover_url"
        :alt="title.code"
        class="w-full h-auto block"
        loading="lazy"
      />
      <div
        v-else
        class="w-full aspect-[2/3] flex items-center justify-center text-neutral-600 text-sm font-medium"
      >
        {{ title.code }}
      </div>

      <!-- 分辨率徽章 -->
      <div
        v-if="title.resolution"
        class="absolute top-2.5 right-2.5 px-2 py-0.5 rounded-full text-[10px] font-medium tracking-wide uppercase backdrop-blur-md"
        :class="resolutionBadgeClass"
      >
        {{ title.resolution }}
      </div>

      <!-- 悬停光晕 + 播放遮罩 -->
      <div class="absolute inset-0 opacity-0 group-hover:opacity-100 transition-opacity duration-300">
        <div class="absolute inset-0 rounded-2xl shadow-[inset_0_0_0_1px_rgba(255,255,255,0.1),0_0_40px_rgba(10,132,255,0.12)]" />
        <div class="absolute inset-0 bg-black/30 rounded-2xl" />
        <div class="absolute inset-0 flex items-center justify-center">
          <button
            v-if="title.magnet"
            class="w-14 h-14 rounded-full bg-white/15 backdrop-blur-md flex items-center justify-center text-white ring-1 ring-white/20 shadow-2xl scale-90 group-hover:scale-100 transition-transform duration-300 hover:bg-white/25"
            @click.stop="$emit('play', title.magnet)"
          >
            <svg width="22" height="22" viewBox="0 0 24 24" fill="currentColor">
              <path d="M8 5v14l11-7z" />
            </svg>
          </button>
        </div>
      </div>
    </div>

    <!-- 信息区 -->
    <div class="mt-3 px-0.5">
      <!-- 第一行：CODE + 拷贝按钮 -->
      <div class="flex items-center justify-between gap-2">
        <h3 class="text-[15px] font-semibold tracking-tight text-white truncate">
          {{ title.code }}
        </h3>
        <button
          v-if="title.magnet"
          class="shrink-0 w-7 h-7 rounded-lg bg-white/5 hover:bg-white/10 flex items-center justify-center text-neutral-400 hover:text-white transition-colors"
          :class="{ 'text-green-400': copied }"
          title="Copy magnet link"
          @click.stop="copyMagnet"
        >
          <svg v-if="!copied" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <rect x="9" y="9" width="13" height="13" rx="2" ry="2" />
            <path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1" />
          </svg>
          <svg v-else width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">
            <polyline points="20 6 9 17 4 12" />
          </svg>
        </button>
      </div>

      <!-- 第二行：风趣改写版简介 -->
      <p
        v-if="title.charming_intro"
        class="text-xs text-neutral-400 line-clamp-2 leading-relaxed mt-1"
      >
        {{ title.charming_intro }}
      </p>

      <!-- 第三行：日期 + cache 状态 -->
      <div class="flex items-center justify-between mt-1.5">
        <span
          v-if="title.date"
          class="text-[11px] text-neutral-500 font-mono tabular-nums"
        >
          {{ formatDate(title.date) }}
        </span>

        <!-- Cache 状态 -->
        <div v-if="torrentStatus" class="flex items-center gap-1.5">
          <template v-if="torrentStatus.head_ready">
            <span class="w-1.5 h-1.5 rounded-full bg-green-500 shadow-[0_0_6px_rgba(34,197,94,0.5)]" />
            <span class="text-[10px] text-green-400 font-medium">Ready</span>
          </template>
          <template v-else-if="torrentStatus.progress > 0">
            <div class="w-10 h-1 rounded-full bg-neutral-800 overflow-hidden">
              <div
                class="h-full rounded-full bg-[#0A84FF] transition-all duration-500"
                :style="{ width: `${Math.min(torrentStatus.progress, 100)}%` }"
              />
            </div>
            <span class="text-[10px] text-neutral-400 font-mono tabular-nums">{{ Math.round(torrentStatus.progress) }}%</span>
          </template>
          <template v-else>
            <span class="w-1.5 h-1.5 rounded-full bg-neutral-700" />
            <span class="text-[10px] text-neutral-600">Idle</span>
          </template>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import type { Title, TorrentStatus } from '~/types/api'

const props = defineProps<{
  title: Title
  starCode: string
}>()

defineEmits<{
  (e: 'play', magnet: string): void
}>()

const config = useRuntimeConfig()
const copied = ref(false)
const torrentStatus = ref<TorrentStatus | null>(null)

// 从 magnet 提取 hash
const hash = computed(() => {
  if (!props.title.magnet) return null
  const match = props.title.magnet.match(/xt=urn:btih:([a-f0-9]{40})/i)
  return match ? match[1].toLowerCase() : null
})

// 拷贝 magnet
async function copyMagnet() {
  if (!props.title.magnet) return
  try {
    await navigator.clipboard.writeText(props.title.magnet)
    copied.value = true
    setTimeout(() => { copied.value = false }, 2000)
  } catch {
    // fallback
    const ta = document.createElement('textarea')
    ta.value = props.title.magnet
    document.body.appendChild(ta)
    ta.select()
    document.execCommand('copy')
    document.body.removeChild(ta)
    copied.value = true
    setTimeout(() => { copied.value = false }, 2000)
  }
}

// 获取 torrent 状态（带随机延迟避免并发冲击）
onMounted(() => {
  if (!hash.value) return
  const delay = Math.random() * 3000
  setTimeout(async () => {
    try {
      const res = await $fetch(`/torrent/status/${hash.value}`, {
        baseURL: config.public.apiBase,
      }) as TorrentStatus
      torrentStatus.value = res
    } catch {
      // 未加入缓存引擎的 torrent 会 404，静默忽略
    }
  }, delay)
})

function formatDate(dateStr: string | undefined): string {
  if (!dateStr) return ''
  const parts = dateStr.split('/')
  if (parts.length === 3) {
    const yy = parts[2].slice(-2)
    return `${yy}/${parts[1]}/${parts[0]}`
  }
  return dateStr
}

const resolutionBadgeClass = computed(() => {
  const r = props.title.resolution?.toLowerCase() || ''
  if (r.includes('4k')) return 'bg-purple-500/80 text-white shadow-[0_0_12px_rgba(168,85,247,0.3)]'
  if (r.includes('fhd') || r.includes('1080')) return 'bg-[#0A84FF]/80 text-white shadow-[0_0_12px_rgba(10,132,255,0.3)]'
  if (r.includes('hd') || r.includes('720')) return 'bg-green-500/80 text-black'
  return 'bg-white/10 text-neutral-300'
})
</script>
