<template>
  <div
    class="min-h-screen bg-black flex items-center justify-center px-6 relative overflow-hidden"
    @mousemove="onMouseMove"
  >
    <!-- 背景光晕 -->
    <div
      class="pointer-events-none absolute inset-0 opacity-40"
      :style="{
        background: `radial-gradient(600px circle at ${mouseX}px ${mouseY}px, rgba(10,132,255,0.12), transparent 60%)`
      }"
    />

    <div class="w-full max-w-[340px] relative z-10">
      <!-- Logo 区域 -->
      <div class="flex flex-col items-center mb-10">
        <div
          class="w-[72px] h-[72px] rounded-[22px] bg-white/[0.08] border border-white/[0.08] flex items-center justify-center mb-5 shadow-lg shadow-black/50"
        >
          <svg
            class="w-9 h-9 text-[#0A84FF]"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            stroke-width="1.5"
            stroke-linecap="round"
            stroke-linejoin="round"
          >
            <rect x="3" y="11" width="18" height="11" rx="2" ry="2" />
            <path d="M7 11V7a5 5 0 0 1 10 0v4" />
          </svg>
        </div>
        <h1
          class="text-[28px] font-semibold text-white tracking-[-0.01em]"
          style="font-family: -apple-system, BlinkMacSystemFont, 'SF Pro Display', 'Segoe UI', sans-serif"
        >
          Star Archive
        </h1>
        <p class="text-sm text-neutral-500 mt-1.5 tracking-wide">
          请输入今日密码
        </p>
      </div>

      <!-- 表单 -->
      <div class="space-y-3">
        <div class="relative group">
          <input
            ref="inputRef"
            v-model="password"
            type="password"
            placeholder="密码"
            class="w-full h-[50px] px-4 rounded-[14px] bg-white/[0.06] border border-white/[0.08] text-white text-[17px] text-center placeholder:text-neutral-600 outline-none transition-all duration-200 focus:bg-white/[0.09] focus:border-[#0A84FF]/50 focus:shadow-[0_0_0_4px_rgba(10,132,255,0.1)]"
            :class="{ 'border-red-500/50 shadow-[0_0_0_4px_rgba(239,68,68,0.1)]': error }"
            @keydown.enter="submit"
          />
        </div>

        <!-- 错误提示 -->
        <p
          v-if="error"
          class="text-[13px] text-red-400 text-center min-h-[1.25em] transition-all"
          :class="shake ? 'animate-shake' : ''"
        >
          {{ error }}
        </p>
        <p v-else class="min-h-[1.25em]" />

        <!-- 进入按钮 -->
        <button
          @click="submit"
          class="w-full h-[50px] rounded-[14px] bg-[#0A84FF] text-white text-[17px] font-medium tracking-wide transition-all duration-200 hover:bg-[#0077ED] active:scale-[0.97] active:opacity-90 shadow-lg shadow-[#0A84FF]/20"
        >
          进入
        </button>
      </div>

      <!-- 底部留白 -->
      <div class="h-8" />
    </div>
  </div>
</template>

<script setup lang="ts">
/* 登录页 — 每日动态密码验证 (Apple 风格) */
definePageMeta({ layout: false })

const password = ref('')
const error = ref('')
const shake = ref(false)
const inputRef = ref<HTMLInputElement>()

const mouseX = ref(0)
const mouseY = ref(0)

function onMouseMove(e: MouseEvent) {
  mouseX.value = e.clientX
  mouseY.value = e.clientY
}

function todayPassword(): string {
  const d = new Date()
  const yy = (d.getFullYear() % 100).toString().padStart(2, '0')
  const mm = (d.getMonth() + 1).toString().padStart(2, '0')
  const dd = d.getDate().toString().padStart(2, '0')
  return `rn${yy}${mm}${dd}${d.getDate() % 2}`
}

function submit() {
  const input = password.value.trim()
  if (input === todayPassword()) {
    const auth = useCookie('claw_auth', { maxAge: 86400, path: '/' })
    auth.value = 'ok'
    navigateTo('/')
  } else {
    error.value = '密码错误'
    shake.value = true
    password.value = ''
    inputRef.value?.focus()
    setTimeout(() => { shake.value = false }, 500)
  }
}

onMounted(() => {
  inputRef.value?.focus()
})
</script>

<style scoped>
@keyframes shake {
  0%, 100% { transform: translateX(0); }
  20% { transform: translateX(-6px); }
  40% { transform: translateX(6px); }
  60% { transform: translateX(-4px); }
  80% { transform: translateX(4px); }
}
.animate-shake {
  animation: shake 0.4s ease-in-out;
}
</style>
