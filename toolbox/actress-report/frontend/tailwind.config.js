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
        'bg-primary': '#0a0a0a',
        'text-primary': '#e5e5e5',
        'text-secondary': '#a3a3a3',
        accent: '#f97316',
      },
    },
  },
  plugins: [],
}
