<template>
  <VitePwaManifest />
  <NuxtLayout>
    <NuxtPage />
  </NuxtLayout>
</template>

<script setup lang="ts">
const { $pwa } = useNuxtApp()

// Log PWA status in dev
if (process.dev && $pwa) {
  console.log('PWA ready:', $pwa.isPWAInstalled)
}
</script>
