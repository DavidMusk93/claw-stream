<template>
  <div class="relative min-h-screen bg-void text-foreground overflow-x-hidden">
    <!-- Ambient light blobs -->
    <div class="fixed inset-0 pointer-events-none z-0 overflow-hidden" aria-hidden="true">
      <div
        class="absolute -top-40 -left-40 w-[600px] h-[600px] rounded-full opacity-[0.07] blur-[120px] animate-blob"
        style="background: radial-gradient(circle, #E11D48 0%, transparent 70%);"
      />
      <div
        class="absolute top-1/3 -right-40 w-[500px] h-[500px] rounded-full opacity-[0.06] blur-[100px] animate-blob-slow"
        style="background: radial-gradient(circle, #D946EF 0%, transparent 70%); animation-delay: -5s;"
      />
      <div
        class="absolute -bottom-40 left-1/3 w-[550px] h-[550px] rounded-full opacity-[0.05] blur-[110px] animate-blob"
        style="background: radial-gradient(circle, #F59E0B 0%, transparent 70%); animation-delay: -10s;"
      />
    </div>

    <!-- Content -->
    <div class="relative z-10">
      <slot />
    </div>
  </div>
</template>
