// Typed access to import.meta.env — the single access point (S0.3 §4).
// Direct import.meta.env use elsewhere is an ESLint error.

export interface AppEnv {
  readonly mode: string
  readonly apiBase: string
  readonly sentryDsn: string | undefined
  readonly release: string
}

export const env: AppEnv = {
  mode: import.meta.env.MODE,
  apiBase: import.meta.env.VITE_API_BASE ?? '/api',
  sentryDsn: import.meta.env.VITE_SENTRY_DSN || undefined,
  release: import.meta.env.VITE_RELEASE ?? 'local',
}
