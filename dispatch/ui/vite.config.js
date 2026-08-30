import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// Dev only. In the container, nginx proxies /api -> api:8000.
// The target is overridable so a second dashboard can be run against a second
// API without editing this file — needed whenever two checkouts (or two
// sessions) are up at once and the default port is already taken.
const target = process.env.VITE_API_TARGET || 'http://localhost:8000'

export default defineConfig({
  plugins: [react()],
  server: {
    // /docs, /redoc and /openapi.json sit outside /api, so they need naming
    // explicitly -- without them the SPA fallback answers and the docs link
    // quietly renders the dashboard again instead of the API reference.
    proxy: {
      '/api': target,
      '/health': target,
      '/docs': target,
      '/redoc': target,
      '/openapi.json': target,
    }
  }
})
