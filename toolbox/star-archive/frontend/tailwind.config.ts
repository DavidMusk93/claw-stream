/** @type {import('tailwindcss').Config} */
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
        ios: {
          black: '#000000',
          'bg-primary': '#000000',
          'bg-secondary': '#1C1C1E',
          'bg-tertiary': '#2C2C2E',
          'bg-elevated': '#1C1C1E',
          'grouped-bg': '#1C1C1E',
          'grouped-secondary': '#2C2C2E',
          'text-primary': '#FFFFFF',
          'text-secondary': '#8E8E93',
          'text-tertiary': '#48484A',
          'separator': '#38383A',
          'separator-opaque': '#38383A',
          blue: '#0A84FF',
          'blue-dark': '#0051D5',
          green: '#30D158',
          red: '#FF453A',
          orange: '#FF9F0A',
          yellow: '#FFD60A',
          indigo: '#5E5CE6',
          purple: '#BF5AF2',
          pink: '#FF375F',
          teal: '#64D2FF',
          gray: '#8E8E93',
          'gray-2': '#636366',
          'gray-3': '#48484A',
          'gray-4': '#3A3A3C',
          'gray-5': '#2C2C2E',
          'gray-6': '#1C1C1E',
        },
      },
      borderRadius: {
        'ios': '1.25rem',      // 20px
        'ios-lg': '1.5rem',    // 24px
        'ios-xl': '2rem',      // 32px
        'ios-sm': '0.625rem',  // 10px
      },
      boxShadow: {
        'ios': '0 4px 24px rgba(0, 0, 0, 0.4)',
        'ios-sm': '0 2px 12px rgba(0, 0, 0, 0.3)',
      },
    },
  },
  plugins: [],
}
