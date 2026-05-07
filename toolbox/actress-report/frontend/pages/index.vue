<template>
  <div class="pl-16 min-h-screen">
    <div class="p-8 max-w-7xl mx-auto">
      <header class="mb-8">
        <h1 class="text-3xl font-bold mb-2">Actress Report</h1>
        <p class="text-sm text-neutral-500">
          Backend: {{ health?.status ?? '...' }} | Cache: {{ metrics?.used_human ?? '...' }}
        </p>
      </header>

      <div v-if="pending" class="text-center py-20 text-neutral-500">
        Loading...
      </div>

      <div v-else-if="error" class="text-center py-20 text-red-400">
        Failed to load data
      </div>

      <div v-else>
        <ActressNav :actresses="actresses ?? []" />
        <ActressCard
          v-for="actress in actresses"
          :key="actress.code"
          :actress="actress"
          @play="openVideo"
        />
      </div>
    </div>

    <VideoModal v-model:open="modalOpen" :hash="activeHash" />
    <CachePanel />
  </div>
</template>

<script setup lang="ts">
const config = useRuntimeConfig()
const { data: health } = useFetch('/api/health', { baseURL: config.public.apiBase })
const { data: metrics } = useFetch('/api/cache/metrics', { baseURL: config.public.apiBase })
const { actresses, pending, error } = useActresses()

const modalOpen = ref(false)
const activeHash = ref('')

function openVideo(magnet: string) {
  const match = magnet.match(/xt=urn:btih:([a-f0-9]{40})/i)
  if (match) {
    activeHash.value = match[1].toLowerCase()
    modalOpen.value = true
  }
}
</script>
