<template>
  <nav class="fixed left-0 top-0 h-screen w-16 bg-[#111] border-r border-white/5 overflow-y-auto py-4 z-50">
    <div class="flex flex-col items-center gap-3">
      <a
        v-for="actress in actresses"
        :key="actress.code"
        :href="`#${actress.code.toLowerCase()}`"
        class="w-10 h-10 rounded-full bg-white/10 hover:bg-white/20 flex items-center justify-center text-xs font-medium transition-colors"
        :title="actress.name"
      >
        {{ actress.name.charAt(0) }}
      </a>
    </div>
  </nav>
</template>

<script setup lang="ts">
interface Actress {
  name: string
  code: string
}

defineProps<{
  actresses: Actress[]
}>()
</script>
