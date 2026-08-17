import * as Sentry from '@sentry/react'
import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { App } from '@app/App'
import { env } from '@shared/config/env'
import '@shared/design-system/tokens/tokens.css'

// DSN-gated: absent in dev = fully disabled (S0.3 §8.1). Errors only, no replay.
if (env.sentryDsn) {
  Sentry.init({ dsn: env.sentryDsn, environment: env.mode, release: env.release })
}

const container = document.getElementById('root')
if (container) {
  createRoot(container).render(
    <StrictMode>
      <App />
    </StrictMode>,
  )
}
