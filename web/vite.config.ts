import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// The engine's API is a local tool bound to 127.0.0.1 (SPEC §15). Dev requests are
// proxied so the app uses same-origin paths in dev and in production alike.
const API = process.env.REFRACT_API ?? 'http://127.0.0.1:8765'

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      '/api': { target: API, changeOrigin: true, ws: true },
    },
  },
})
