<template>
  <div class="min-h-screen bg-black flex items-center justify-center px-4">
    <div class="w-full max-w-xs text-center">
      <div class="mb-8">
        <div class="w-16 h-16 mx-auto rounded-2xl bg-neutral-900 border border-white/10 flex items-center justify-center mb-4">
          <svg class="w-8 h-8 text-[#0A84FF]" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <rect x="3" y="11" width="18" height="11" rx="2" ry="2"/>
            <path d="M7 11V7a5 5 0 0 1 10 0v4"/>
          </svg>
        </div>
        <h1 class="text-xl font-semibold text-white tracking-tight">Star Archive</h1>
        <p class="text-sm text-neutral-500 mt-1">请输入今日密码</p>
      </div>

      <div class="space-y-4">
        <input
          ref="inputRef"
          v-model="password"
          type="password"
          placeholder="密码"
          class="w-full px-4 py-3 rounded-xl bg-neutral-900 border border-white/10 text-white text-center text-base placeholder-neutral-600 outline-none focus:border-[#0A84FF] transition-colors"
          @keydown.enter="submit"
        />
        <p v-if="error" class="text-sm text-red-500 min-h-[1.25em]">{{ error }}</p>
        <button
          @click="submit"
          class="w-full py-3 rounded-xl bg-[#0A84FF] text-white font-medium text-sm hover:bg-[#0066CC] active:scale-[0.98] transition-all"
        >
          进入
        </button>
      </div>

      <p class="text-[11px] text-neutral-700 mt-6">
        格式: rn + 年月日 + 奇偶 (1/0)
      </p>
    </div>
  </div>
</template>

<script setup lang="ts">
/* 登录页 — 每日动态密码验证 */
definePageMeta({ layout: false })

const password = ref('')
const error = ref('')
const inputRef = ref<HTMLInputElement>()

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
    password.value = ''
    inputRef.value?.focus()
  }
}

onMounted(() => {
  inputRef.value?.focus()
})
</script>
