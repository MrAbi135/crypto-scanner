// Level-gated console wrapper with a Sentry breadcrumb hook (S0.3 §4).
// Silent in test; NEVER logs payload bodies — only event keys + small context.
import * as Sentry from '@sentry/react'
import { env } from '@shared/config/env'

type Level = 'debug' | 'info' | 'warn' | 'error'
type Context = Record<string, string | number | boolean>

const ORDER: Record<Level, number> = { debug: 0, info: 1, warn: 2, error: 3 }
const SENTRY_LEVEL: Record<Level, 'debug' | 'info' | 'warning' | 'error'> = {
  debug: 'debug',
  info: 'info',
  warn: 'warning',
  error: 'error',
}
const threshold = env.mode === 'production' ? ORDER.info : ORDER.debug

function emit(level: Level, event: string, context?: Context): void {
  if (env.mode === 'test') return
  if (ORDER[level] < threshold) return
  Sentry.addBreadcrumb({
    level: SENTRY_LEVEL[level],
    message: event,
    ...(context ? { data: context } : {}),
  })
  console[level](event, context ?? {})
}

export const logger = {
  debug: (event: string, context?: Context): void => emit('debug', event, context),
  info: (event: string, context?: Context): void => emit('info', event, context),
  warn: (event: string, context?: Context): void => emit('warn', event, context),
  error: (event: string, context?: Context): void => emit('error', event, context),
}
