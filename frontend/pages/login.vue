<template>
  <div class="min-h-screen bg-void flex items-center justify-center px-6 relative overflow-hidden">
    <div class="w-full max-w-[380px] relative z-10">
      <!-- Logo area -->
      <div class="flex flex-col items-center mb-10">
        <img src="/logo.png" alt="Star Archive logo" class="w-[96px] h-[96px] mb-6 drop-shadow-sm rounded-2xl" />
        <h1
          class="text-[32px] font-semibold text-foreground tracking-[-0.02em]"
        >
          Star Archive
        </h1>
        <p class="text-[15px] text-foreground-muted mt-2 font-light">
          {{ randomGreeting }}
        </p>
      </div>

      <!-- Form -->
      <div class="space-y-3">
        <div class="relative group">
          <input
            ref="inputRef"
            v-model="password"
            :type="showPassword ? 'text' : 'password'"
            placeholder="🔑 Passcode"
            class="w-full h-[52px] px-12 rounded-[16px] bg-white border border-black/[0.08] text-foreground text-[17px] text-center placeholder:text-foreground-muted/40 outline-none transition-all duration-200 focus:border-[#ff375f]/50 focus:shadow-[0_0_0_4px_rgba(255,55,95,0.1)]"
            :class="{ 'border-[#ff375f]/50 shadow-[0_0_0_4px_rgba(255,55,95,0.1)]': error }"
            @keydown.enter="submit"
          />
          <!-- Toggle visibility -->
          <button
            type="button"
            class="absolute right-3 top-1/2 -translate-y-1/2 w-9 h-9 flex items-center justify-center text-foreground-muted/50 hover:text-foreground-muted transition-colors"
            @click="showPassword = !showPassword"
          >
            {{ showPassword ? '🙈' : '👁️' }}
          </button>
        </div>

        <!-- Error message -->
        <p
          v-if="error"
          class="text-[14px] text-[#ff375f] text-center min-h-[1.25em] transition-all"
          :class="shake ? 'animate-shake' : ''"
        >
          {{ error }} 👻
        </p>
        <p v-else class="min-h-[1.25em]" />

        <!-- Enter button -->
        <button
          @click="submit"
          class="w-full h-[52px] rounded-[16px] bg-[#ff375f] text-white text-[17px] font-semibold transition-all duration-200 hover:brightness-110 active:scale-[0.97] flex items-center justify-center gap-2"
        >
          <span>🚀 Enter</span>
        </button>
      </div>

      <!-- Footer -->
      <div class="h-10 flex items-center justify-center">
        <span class="text-[12px] text-foreground-muted/40">{{ randomFooter }}</span>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
/* Login page — daily rotating password validation */
definePageMeta({ layout: false })

const password = ref('')
const showPassword = ref(false)
const error = ref('')
const shake = ref(false)
const inputRef = ref<HTMLInputElement>()

// Whimsical greetings rotation
const greetings = [
  'Say the word and you\'re in',
  'Open sesame... no wait, this one',
  'Shhh, keep it down',
  'Welcome to the grown-up world',
  'Got your invitation?',
  'Wrong passcode summons ghosts',
  'Welcome back, old friend',
]
const randomGreeting = greetings[Math.floor(Math.random() * greetings.length)]

// Whimsical footer
const footers = [
  '🔮 Today\'s fortune: great for viewing',
  '🍿 Don\'t forget the popcorn',
  '👻 Wrong passwords summon ghosts',
  '🎬 Please silence your devices',
  '🌙 It\'s late, keep it quiet',
]
const randomFooter = footers[Math.floor(Math.random() * footers.length)]

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
    error.value = 'Incorrect passcode'
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
