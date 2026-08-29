export default defineNuxtConfig({
  devtools: { enabled: true },
  css: ['~/assets/css/main.css'],
  app: {
    head: {
      link: [
        { rel: 'preconnect', href: 'https://fonts.googleapis.com' },
        { rel: 'preconnect', href: 'https://fonts.gstatic.com', crossorigin: '' },
        { rel: 'stylesheet', href: 'https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&display=swap' },
        { rel: 'icon', type: 'image/png', href: '/icon.png' },
        { rel: 'apple-touch-icon', href: '/icon-192x192.png' },
      ],
    },
  },
  modules: ['@pinia/nuxt', '@vite-pwa/nuxt'],
  // Flat component auto-import: nested dirs (star/, ui/, cache/...) register
  // WITHOUT the dir prefix, so <Skeleton> resolves ui/Skeleton.vue. With the
  // default prefixing, ui/Skeleton.vue registers as UiSkeleton and <Skeleton>
  // silently rendered as an empty comment — causing massive hydration
  // mismatches (SSR comment vs client vnode) that intermittently left the
  // first page load stuck on empty placeholders.
  components: [{ path: '~/components', pathPrefix: false }],
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
      theme_color: '#F5F5F7',
      background_color: '#F5F5F7',
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
      // Do NOT precache HTML: Nuxt SSR pages are dynamic and must always be
      // fetched fresh. Precaching HTML caused old app shells to be served after
      // deploy, making "refresh" show stale or empty pages.
      globPatterns: ['**/*.{js,css,png,svg,ico,woff,woff2,json}'],
      cleanupOutdatedCaches: true,
      clientsClaim: true,
      skipWaiting: true,
      runtimeCaching: [
        {
          // Stars list must be fresh after every sync/add/delete. Keep cache
          // lifetime extremely short so a refresh always sees the latest data.
          urlPattern: /^\/api\/stars/,
          handler: 'NetworkFirst',
          options: {
            cacheName: 'stars-cache',
            expiration: { maxEntries: 5, maxAgeSeconds: 1 },
          },
        },
        {
          // Covers (full + thumb) are immutable per code and keyed by code in
          // the URL. Keep the entry budget above the library size so browsing
          // the whole catalog never evicts earlier covers.
          urlPattern: /^\/(images|api\/cover)\/.*/,
          handler: 'StaleWhileRevalidate',
          options: {
            cacheName: 'image-cache',
            expiration: { maxEntries: 1500, maxAgeSeconds: 2592000 },
          },
        },
        {
          urlPattern: /^\/api\/health/,
          handler: 'StaleWhileRevalidate',
          options: {
            cacheName: 'static-api-cache',
            expiration: { maxEntries: 5, maxAgeSeconds: 2592000 },
          },
        },
        {
          urlPattern: /^\/api\/.*/,
          handler: 'NetworkFirst',
          options: {
            cacheName: 'api-cache',
            expiration: { maxEntries: 50, maxAgeSeconds: 60 },
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
    server: {
      allowedHosts: ['cc.guohuasun.com', 'localhost', '127.0.0.1'],
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
      '/images': {
        target: 'http://localhost:8765',
        changeOrigin: true,
      },
    },
    routeRules: {
      '/api/**': { proxy: 'http://127.0.0.1:8765/api/**' },
      '/stream/**': { proxy: 'http://127.0.0.1:8765/stream/**' },
      '/torrent/**': { proxy: 'http://127.0.0.1:8765/torrent/**' },
      '/images/**': { proxy: 'http://127.0.0.1:8765/images/**' },
    },
  },
})
