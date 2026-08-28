import { fileURLToPath } from 'node:url'
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// Path aliases mirror tsconfig; dev proxy sends /api and /internal to the api
// process (single-origin dev, mirroring the Caddy prod topology — TAD §22).
export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      '@app': fileURLToPath(new URL('./src/app', import.meta.url)),
      '@features': fileURLToPath(new URL('./src/features', import.meta.url)),
      '@entities': fileURLToPath(new URL('./src/entities', import.meta.url)),
      '@services': fileURLToPath(new URL('./src/services', import.meta.url)),
      '@shared': fileURLToPath(new URL('./src/shared', import.meta.url)),
      // Test-only helpers. Aliased because the lint rule forbids deep
      // relative imports, and `src/test` is not one of the architectural
      // layers `boundaries` governs -- it is scaffolding for the ones that are.
      '@test': fileURLToPath(new URL('./src/test', import.meta.url)),
    },
  },
  server: {
    proxy: {
      '/api': 'http://localhost:8000',
      '/internal': 'http://localhost:8000',
    },
  },
})
