export default defineNuxtConfig({
  devtools: { enabled: true },
  css: ['~/assets/css/main.css'],
  modules: ['@pinia/nuxt', '@nuxtjs/tailwindcss'],
  runtimeConfig: {
    apiBase: 'http://localhost:8765',
    public: {
      apiBase: 'http://localhost:8765',
    },
  },
  nitro: {
    devProxy: {
      '/api': {
        target: 'http://localhost:8765',
        changeOrigin: true,
      },
      '/stream': {
        target: 'http://localhost:8765',
        changeOrigin: true,
      },
      '/torrent': {
        target: 'http://localhost:8765',
        changeOrigin: true,
      },
    },
  },
})
