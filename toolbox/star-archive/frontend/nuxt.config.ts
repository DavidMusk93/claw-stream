export default defineNuxtConfig({
  devtools: { enabled: true },
  css: ['~/assets/css/main.css'],
  app: {
    head: {
      link: [
        { rel: 'preconnect', href: 'https://fonts.googleapis.com' },
        { rel: 'preconnect', href: 'https://fonts.gstatic.com', crossorigin: '' },
        { rel: 'stylesheet', href: 'https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&family=Playfair+Display:ital,wght@0,400;0,500;0,600;0,700;1,400;1,500&display=swap' },
      ],
    },
  },
  modules: ['@pinia/nuxt', '@vite-pwa/nuxt'],
  postcss: {
    plugins: {
      tailwindcss: {},
      autoprefixer: {},
    },
  },
  pwa: {
    registerType: 'autoUpdate',
    manifest: {
      name: 'Star Archive',
      short_name: 'StarArchive',
      description: 'Star collection with video streaming',
      theme_color: '#0a0a0a',
      background_color: '#0a0a0a',
      display: 'standalone',
      orientation: 'portrait',
      scope: '/',
      start_url: '/',
      icons: [
        { src: '/icon-192x192.png', sizes: '192x192', type: 'image/png' },
        { src: '/icon-512x512.png', sizes: '512x512', type: 'image/png' },
      ],
    },
    workbox: {
      navigateFallback: '/',
      globPatterns: ['**/*.{js,css,html,png,svg,ico}'],
      cleanupOutdatedCaches: true,
      clientsClaim: true,
      skipWaiting: true,
      runtimeCaching: [
        {
          urlPattern: /^\/api\/stars/,
          handler: 'StaleWhileRevalidate',
          options: {
            cacheName: 'stars-cache',
            expiration: { maxEntries: 5, maxAgeSeconds: 3600 },
          },
        },
        {
          urlPattern: /^\/api\/(health|cover)/,
          handler: 'CacheFirst',
          options: {
            cacheName: 'static-api-cache',
            expiration: { maxEntries: 200, maxAgeSeconds: 2592000 },
          },
        },
        {
          urlPattern: /^\/api\/.*/,
          handler: 'NetworkFirst',
          options: {
            cacheName: 'api-cache',
            expiration: { maxEntries: 50, maxAgeSeconds: 300 },
          },
        },
        {
          urlPattern: /^\/(images|api\/cover)\/.*/,
          handler: 'CacheFirst',
          options: {
            cacheName: 'image-cache',
            expiration: { maxEntries: 500, maxAgeSeconds: 2592000 },
          },
        },
      ],
    },
    devOptions: {
      enabled: true,
      type: 'module',
    },
  },
  runtimeConfig: {
    apiBase: '',
    public: {
      apiBase: '',
    },
  },
  vite: {
    build: {
      cssCodeSplit: true,
    },
  },
  experimental: {
    inlineSSRStyles: () => true,
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
    routeRules: {
      '/api/**': { proxy: 'http://127.0.0.1:8765/api/**' },
      '/stream/**': { proxy: 'http://127.0.0.1:8765/stream/**' },
      '/torrent/**': { proxy: 'http://127.0.0.1:8765/torrent/**' },
    },
  },
})
