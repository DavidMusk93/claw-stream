<template>
  <div
    class="min-h-screen bg-void flex items-center justify-center px-6 relative overflow-hidden"
    @mousemove="onMouseMove"
  >
    <!-- 背景光晕 -->
    <div
      class="pointer-events-none absolute inset-0 opacity-40"
      :style="{
        background: `radial-gradient(600px circle at ${mouseX}px ${mouseY}px, rgba(225,29,72,0.15), transparent 60%)`
      }"
    />

    <div class="w-full max-w-[340px] relative z-10">
      <!-- Logo 区域 -->
      <div class="flex flex-col items-center mb-10">
        <div
          class="w-[72px] h-[72px] rounded-[22px] glass flex items-center justify-center mb-5 shadow-glass text-3xl"
        >
          🔒
        </div>
        <h1
          class="text-[28px] font-display font-semibold text-foreground tracking-[-0.01em]"
        >
          Star Archive
        </h1>
        <p class="text-sm text-foreground-muted mt-1.5 tracking-wide font-light">
          {{ randomGreeting }}
        </p>
      </div>

      <!-- 表单 -->
      <div class="space-y-3">
        <div class="relative group">
          <input
            ref="inputRef"
            v-model="password"
            :type="showPassword ? 'text' : 'password'"
            placeholder="🔑 暗号"
            class="w-full h-[50px] px-12 rounded-[14px] bg-white/[0.04] border border-white/[0.08] text-foreground text-[17px] text-center placeholder:text-foreground-muted/40 outline-none transition-all duration-200 focus:bg-white/[0.07] focus:border-rose/40 focus:shadow-[0_0_0_4px_rgba(225,29,72,0.1)]"
            :class="{ 'border-rose/40 shadow-[0_0_0_4px_rgba(225,29,72,0.1)]': error }"
            @keydown.enter="submit"
          />
          <!-- 查看明文切换 -->
          <button
            type="button"
            class="absolute right-3 top-1/2 -translate-y-1/2 w-8 h-8 flex items-center justify-center text-foreground-muted/50 hover:text-foreground-muted transition-colors"
            @click="showPassword = !showPassword"
          >
            {{ showPassword ? '🙈' : '👁️' }}
          </button>
        </div>

        <!-- 错误提示 -->
        <p
          v-if="error"
          class="text-[13px] text-rose text-center min-h-[1.25em] transition-all"
          :class="shake ? 'animate-shake' : ''"
        >
          {{ error }} 👻
        </p>
        <p v-else class="min-h-[1.25em]" />

        <!-- 进入按钮 -->
        <button
          @click="submit"
          class="w-full h-[50px] rounded-[14px] bg-gradient-to-r from-rose to-violet text-white text-[17px] font-medium tracking-wide transition-all duration-200 hover:brightness-110 active:scale-[0.97] active:opacity-90 shadow-glass hover:shadow-rose-glow flex items-center justify-center gap-2"
        >
          <span>🚀 进入</span>
        </button>
      </div>

      <!-- 底部 -->
      <div class="h-8 flex items-center justify-center">
        <span class="text-[11px] text-foreground-muted/30">{{ randomFooter }}</span>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
/* 登录页 — 每日动态密码验证 */
definePageMeta({ layout: false })

const password = ref('')
const showPassword = ref(false)
const error = ref('')
const shake = ref(false)
const inputRef = ref<HTMLInputElement>()

const mouseX = ref(0)
const mouseY = ref(0)

// 搞怪 greetings 轮换
const greetings = [
  '暗号对上了就放你进去',
  '芝麻开门...不对，是这个',
  '嘘，小声点',
  '欢迎来到大人世界',
  '你有邀请函吗？',
  '密码不对会被幽灵抓走',
  '欢迎回来，老伙计',
]
const randomGreeting = greetings[Math.floor(Math.random() * greetings.length)]

// 搞怪 footer
const footers = [
  '🔮 今日运势：宜观影',
  '🍿 记得带爆米花',
  '👻 错误的密码会召唤幽灵',
  '🎬 开场前请关闭闪光灯',
  '🌙 夜深了，小声点',
]
const randomFooter = footers[Math.floor(Math.random() * footers.length)]

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
    error.value = '密码不对哦'
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
