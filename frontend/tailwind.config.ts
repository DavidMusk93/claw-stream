import type { Config } from 'tailwindcss'

export default {
  content: [
    './components/**/*.{vue,js,ts}',
    './pages/**/*.{vue,js,ts}',
    './layouts/**/*.{vue,js,ts}',
    './composables/**/*.{js,ts}',
    './app.vue',
  ],
  theme: {
    extend: {
      colors: {
        void: '#000000',
        surface: 'rgba(255,255,255,0.03)',
        'surface-elevated': 'rgba(255,255,255,0.06)',
        foreground: '#F8FAFC',
        'foreground-muted': '#8A8F98',
        rose: {
          DEFAULT: '#E11D48',
          dark: '#BE123C',
          light: '#FB7185',
          glow: 'rgba(225,29,72,0.25)',
        },
        violet: {
          DEFAULT: '#D946EF',
          dark: '#A21CAF',
          light: '#E879F9',
          glow: 'rgba(217,70,239,0.25)',
        },
        amber: {
          DEFAULT: '#F59E0B',
          dark: '#D97706',
          light: '#FBBF24',
          glow: 'rgba(245,158,11,0.25)',
        },
        glass: {
          border: 'rgba(255,255,255,0.06)',
          'border-strong': 'rgba(255,255,255,0.12)',
          bg: 'rgba(255,255,255,0.03)',
          'bg-hover': 'rgba(255,255,255,0.06)',
        },
      },
      fontFamily: {
        display: ['-apple-system', 'BlinkMacSystemFont', '"SF Pro Display"', '"SF Pro Text"', 'Inter', 'system-ui', 'sans-serif'],
        sans: ['-apple-system', 'BlinkMacSystemFont', '"SF Pro Text"', '"SF Pro Display"', 'Inter', 'system-ui', 'sans-serif'],
      },
      borderRadius: {
        'glass': '1.25rem',
        'glass-lg': '1.5rem',
        'glass-xl': '2rem',
        'glass-sm': '0.75rem',
      },
      boxShadow: {
        'rose-glow': '0 0 40px rgba(225,29,72,0.15)',
        'violet-glow': '0 0 40px rgba(217,70,239,0.15)',
        'amber-glow': '0 0 40px rgba(245,158,11,0.15)',
        'glass': '0 8px 32px rgba(0,0,0,0.4)',
        'glass-sm': '0 4px 16px rgba(0,0,0,0.3)',
      },
      backdropBlur: {
        'glass': '20px',
      },
      animation: {
        'blob': 'blob 20s infinite ease-in-out',
        'blob-slow': 'blob 30s infinite ease-in-out',
        'fade-up': 'fadeUp 0.6s ease-out forwards',
        'pulse-slow': 'pulse 4s cubic-bezier(0.4, 0, 0.6, 1) infinite',
      },
      keyframes: {
        blob: {
          '0%, 100%': { transform: 'translate(0, 0) scale(1)' },
          '33%': { transform: 'translate(30px, -50px) scale(1.1)' },
          '66%': { transform: 'translate(-20px, 20px) scale(0.9)' },
        },
        fadeUp: {
          '0%': { opacity: '0', transform: 'translateY(20px)' },
          '100%': { opacity: '1', transform: 'translateY(0)' },
        },
      },
    },
  },
  safelist: [
    'bg-rose', 'text-rose', 'bg-rose/10', 'bg-rose/20',
    'bg-violet', 'text-violet', 'bg-violet/10', 'bg-violet/20',
    'bg-amber', 'text-amber', 'bg-amber/10', 'bg-amber/20',
    'shadow-rose-glow', 'shadow-violet-glow', 'shadow-amber-glow',
    'bg-glass-bg', 'border-glass-border',
    'font-display', 'font-sans',
  ],
  plugins: [],
} as Config
