import { defineConfig, mergeConfig } from 'vitest/config'
import viteConfig from './vite.config'

// Reuse vite plugins + aliases; add the jsdom test environment.
export default mergeConfig(
  viteConfig,
  defineConfig({
    test: {
      environment: 'jsdom',
      include: ['src/**/*.test.{ts,tsx}'],
      // Registers Testing Library's per-test unmount. See src/test/setup.ts
      // for why it has to be explicit here.
      setupFiles: ['./src/test/setup.ts'],
    },
  }),
)
