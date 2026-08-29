<template>
  <div class="fixed bottom-4 left-4 z-50">
    <!-- FAB -->
    <button
      class="relative w-12 h-12 rounded-full bg-white border border-black/[0.06] shadow-lg flex items-center justify-center text-foreground transition-all hover:shadow-xl active:scale-[0.95]"
      title="Events"
      @click="toggle"
    >
      <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
        <polyline points="22 12 18 12 15 21 9 3 6 12 2 12" />
      </svg>
      <span
        v-if="errorCount > 0"
        class="absolute -top-0.5 -right-0.5 min-w-[18px] h-[18px] px-1 rounded-full bg-[#ff453a] border-2 border-white text-white text-[10px] font-bold flex items-center justify-center"
      >{{ errorCount > 99 ? '99+' : errorCount }}</span>
      <span
        v-else-if="syncRunning"
        class="absolute -top-0.5 -right-0.5 w-3.5 h-3.5 rounded-full bg-[#0a84ff] border-2 border-white shadow-[0_0_8px_rgba(10,132,255,0.4)] animate-pulse"
      />
    </button>

    <!-- Panel -->
    <Transition name="panel">
      <div
        v-if="open"
        class="absolute bottom-14 left-0 w-[92vw] sm:w-[420px] max-h-[calc(100dvh-140px)] bg-white/95 backdrop-blur-xl border border-black/[0.06] rounded-2xl shadow-2xl overflow-hidden flex flex-col"
      >
        <!-- Header -->
        <div class="flex items-center justify-between px-4 py-3 border-b border-black/[0.06]">
          <h3 class="text-[15px] font-semibold text-foreground">Events</h3>
          <button class="text-foreground-muted hover:text-foreground transition-colors" @click="open = false">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round">
              <line x1="18" y1="6" x2="6" y2="18" /><line x1="6" y1="6" x2="18" y2="18" />
            </svg>
          </button>
        </div>

        <!-- Sync status -->
        <div class="px-4 py-3 border-b border-black/[0.06] bg-black/[0.02]">
          <div class="flex items-center justify-between text-[13px]">
            <span class="text-foreground-muted">Last update</span>
            <span class="font-medium text-foreground">{{ lastRunText }}</span>
          </div>
          <div class="flex items-center justify-between text-[13px] mt-1">
            <span class="text-foreground-muted">Next scheduled</span>
            <span class="font-medium text-foreground">{{ nextRunText }}</span>
          </div>
          <div v-if="lastRun" class="mt-1.5 text-[12px] text-foreground-muted">
            <span :class="lastRun.status === 'success' ? 'text-[#30d158]' : 'text-[#ff453a]'">
              {{ lastRun.status === 'success' ? '✓' : '✗' }} {{ lastRun.trigger }}
            </span>
            <span v-if="lastRun.status === 'success'">
              — +{{ lastRun.total_new }} new, {{ lastRun.total_updated }} updated<span v-if="lastRun.failed_count">, {{ lastRun.failed_count }} failed</span>
            </span>
            <span v-else-if="lastRun.error" class="line-clamp-1"> — {{ lastRun.error }}</span>
          </div>
        </div>

        <!-- Event list -->
        <div class="flex-1 overflow-y-auto px-2 py-2">
          <div v-if="entries.length === 0" class="py-10 text-center text-[13px] text-foreground-muted">
            No events yet
          </div>
          <div
            v-for="(e, i) in entries"
            :key="e.ts + '-' + i"
            class="flex items-start gap-2.5 px-2 py-2 rounded-lg hover:bg-black/[0.03]"
          >
            <span
              class="mt-1.5 w-2 h-2 rounded-full shrink-0"
              :class="{
                'bg-[#30d158]': e.state === 'success',
                'bg-[#ff453a]': e.state === 'error',
                'bg-[#0a84ff] animate-pulse': e.state === 'running',
                'bg-black/25': !e.state || e.state === 'info',
              }"
            />
            <div class="flex-1 min-w-0">
              <div class="text-[13px] text-foreground leading-snug">{{ e.title }}</div>
              <div v-if="e.detail" class="text-[12px] text-foreground-muted leading-snug break-words">{{ e.detail }}</div>
            </div>
            <span class="text-[11px] text-foreground-muted/60 tabular-nums shrink-0 mt-0.5">{{ fmtTime(e.ts) }}</span>
          </div>
        </div>
      </div>
    </Transition>
  </div>
</template>

<script setup lang="ts">
interface SyncRun {
  id: number
  trigger: string
  status: string
  started_at?: string
  finished_at?: string
  total_new: number
  total_updated: number
  failed_count: number
  error?: string
}

const open = ref(false)
const { entries } = useEventLog()

const errorCount = computed(() => entries.value.filter(e => e.state === 'error').length)

const syncRunning = ref(false)
const lastRun = ref<SyncRun | null>(null)
const nextScheduledAt = ref<number | null>(null)
const intervalHours = ref(6)

const lastRunText = computed(() => {
  if (syncRunning.value) return 'Running…'
  if (!lastRun.value?.finished_at) return 'Never'
  return lastRun.value.finished_at
})

const nextRunText = computed(() => {
  if (!nextScheduledAt.value) return '—'
  const mins = Math.max(0, Math.round((nextScheduledAt.value * 1000 - Date.now()) / 60000))
  if (mins >= 60) return `in ${(mins / 60).toFixed(1)}h`
  return `in ${mins}min`
})

async function fetchSyncStatus() {
  try {
    const config = useRuntimeConfig()
    const data = await $fetch<any>('/api/stars/sync', { baseURL: config.public.apiBase })
    syncRunning.value = !!data.running
    lastRun.value = data.last_run ?? null
    nextScheduledAt.value = data.next_scheduled_at ?? null
    intervalHours.value = data.sync_interval_hours ?? 6
  } catch {
    // panel is best-effort
  }
}

function toggle() {
  open.value = !open.value
  if (open.value) fetchSyncStatus()
}

function fmtTime(ts: number): string {
  const d = new Date(ts)
  return `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}:${String(d.getSeconds()).padStart(2, '0')}`
}

const { add } = useEventLog()
const unsubs: (() => void)[] = []

onMounted(() => {
  fetchSyncStatus()

  // Live sync events → panel entries
  unsubs.push(onServerEvent('sync.started', (d: any) => {
    syncRunning.value = true
    add({ kind: 'sync', title: `Sync started (${d?.trigger ?? 'manual'})`, state: 'running' })
  }))
  unsubs.push(onServerEvent('sync.completed', (d: any) => {
    syncRunning.value = false
    fetchSyncStatus()
    add({
      kind: 'sync',
      title: `Sync completed — +${d?.total_new ?? 0} new`,
      detail: `elapsed ${d?.elapsed ?? '?'}s${d?.failed?.length ? `, failed: ${d.failed.join(', ')}` : ''}`,
      state: d?.failed?.length ? 'info' : 'success',
    })
  }))
  unsubs.push(onServerEvent('sync.error', (d: any) => {
    syncRunning.value = false
    fetchSyncStatus()
    add({ kind: 'sync', title: 'Sync failed', detail: d?.error, state: 'error' })
  }))
  unsubs.push(onServerEvent('star.ready', (d: any) => {
    add({ kind: 'info', title: `Star ready: ${d?.name ?? d?.code}`, detail: `${d?.titles_count ?? 0} titles`, state: 'success' })
  }))

  // Page errors → panel
  const onError = (ev: ErrorEvent) => {
    add({ kind: 'error', title: 'Page error', detail: ev.message, state: 'error' })
  }
  const onRejection = (ev: PromiseRejectionEvent) => {
    add({ kind: 'error', title: 'Unhandled rejection', detail: String(ev.reason).slice(0, 200), state: 'error' })
  }
  window.addEventListener('error', onError)
  window.addEventListener('unhandledrejection', onRejection)
  unsubs.push(() => {
    window.removeEventListener('error', onError)
    window.removeEventListener('unhandledrejection', onRejection)
  })
})

onUnmounted(() => {
  unsubs.forEach(fn => fn())
})
</script>

<style scoped>
.panel-enter-active,
.panel-leave-active {
  transition: opacity 0.18s ease, transform 0.18s ease;
}
.panel-enter-from,
.panel-leave-to {
  opacity: 0;
  transform: translateY(8px) scale(0.98);
}
</style>
