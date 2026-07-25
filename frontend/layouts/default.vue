<template>
  <div class="relative min-h-screen bg-void text-foreground overflow-x-hidden">
    <!-- Content: flat, content-first — no ambient decoration (Apple: every element earns its place) -->
    <div class="relative z-10">
      <slot />
    </div>
  </div>
</template>
