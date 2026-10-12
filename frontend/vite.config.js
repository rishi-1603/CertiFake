import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  // Arena/preview hosts are proxied to the dev server. Vite's default host
  // allowlist rejects that browser-facing origin even when --host 0.0.0.0 is
  // set, so allow the preview hostname explicitly for local development.
  server: {
    host: '0.0.0.0',
    allowedHosts: true,
  },
})
