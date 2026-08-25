<template>
  <Transition
    enter-active-class="transition duration-300 ease-out"
    enter-from-class="translate-y-4 opacity-0"
    enter-to-class="translate-y-0 opacity-100"
    leave-active-class="transition duration-200 ease-in"
    leave-from-class="translate-y-0 opacity-100"
    leave-to-class="translate-y-4 opacity-0"
  >
    <div
      v-if="visible"
      class="fixed top-16 left-1/2 -translate-x-1/2 z-50 flex items-start gap-3.5 px-5 py-4 rounded-2xl shadow-2xl max-w-md w-[92vw] bg-white border border-black/[0.06]"
      :class="state === 'error' ? 'border-[#ff453a]/30' : ''"
      @click="state !== 'running' && $emit('dismiss')"
    >
      <!-- Status icon -->
      <span
        class="w-6 h-6 rounded-full flex items-center justify-center shrink-0 mt-0.5"
        :class="iconBgClass"
      >
        <svg
          v-if="state === 'running'"
          class="animate-spin"
          width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round"
        >
          <path d="M21 12a9 9 0 1 1-6.22-8.56" />
        </svg>
        <svg v-else-if="state === 'success'" width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round">
          <polyline points="20 6 9 17 4 12" />
        </svg>
        <svg v-else width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">
          <circle cx="12" cy="12" r="10" />
          <line x1="12" y1="8" x2="12" y2="12" />
          <line x1="12" y1="16" x2="12.01" y2="16" />
        </svg>
      </span>

      <!-- Text + progress -->
      <div class="flex-1 min-w-0">
        <div class="flex items-baseline justify-between gap-2">
          <p class="text-[14px] font-semibold text-foreground leading-snug">{{ title }}</p>
          <span v-if="progressText" class="text-[12px] font-medium text-foreground-muted tabular-nums shrink-0">
            {{ progressText }}
          </span>
        </div>
        <p v-if="detail" class="text-[13px] text-foreground-muted mt-0.5 leading-snug truncate">{{ detail }}</p>

        <!-- Progress bar -->
        <div v-if="state === 'running'" class="mt-2 h-1 rounded-full bg-black/[0.06] overflow-hidden">
          <div
            class="h-full rounded-full bg-[#ff375f] transition-[width] duration-500 ease-out"
            :style="{ width: `${Math.max(4, Math.round(fraction * 100))}%` }"
          />
        </div>
      </div>

      <button
        v-if="state !== 'running'"
        class="text-foreground-muted/60 hover:text-foreground transition-colors shrink-0 mt-0.5"
        @click.stop="$emit('dismiss')"
      >
        <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">
          <line x1="18" y1="6" x2="6" y2="18" />
          <line x1="6" y1="6" x2="18" y2="18" />
        </svg>
      </button>
    </div>
  </Transition>
</template>

<script setup lang="ts">
const props = defineProps<{
  visible: boolean
  state: 'running' | 'success' | 'error'
  title: string
  detail?: string
  progressText?: string
  fraction: number
}>()

defineEmits<{
  (e: 'dismiss'): void
}>()

const iconBgClass = computed(() => {
  switch (props.state) {
    case 'running': return 'bg-[#ff375f]/10 text-[#ff375f]'
    case 'success': return 'bg-[#30d158]/15 text-[#30d158]'
    case 'error': return 'bg-[#ff453a]/15 text-[#ff453a]'
  }
})
</script>
