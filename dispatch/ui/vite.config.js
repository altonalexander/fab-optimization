import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  server: {
    // Dev only. In the container, nginx proxies /api -> api:8000.
    proxy: { '/api': 'http://localhost:8000', '/health': 'http://localhost:8000' }
  }
})
