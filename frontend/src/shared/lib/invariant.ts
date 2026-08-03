// invariant(cond, msg): dev-throws, prod-reports-to-Sentry (S0.3 §4).
import * as Sentry from '@sentry/react'
import { env } from '@shared/config/env'

export function invariant(condition: unknown, message: string): asserts condition {
  if (condition) return
  const error = new Error(`Invariant failed: ${message}`)
  if (env.mode === 'production') {
    Sentry.captureException(error)
    return
  }
  throw error
}
