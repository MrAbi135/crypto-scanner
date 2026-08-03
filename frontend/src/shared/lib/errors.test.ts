import { describe, expect, it } from 'vitest'
import { AppError, fromEnvelope } from './errors'

describe('errors', () => {
  it('builds an AppError from an API envelope', () => {
    const error = fromEnvelope({
      code: 'RATE_LIMITED',
      message: 'slow down',
      correlation_id: 'cid-1',
    })
    expect(error).toBeInstanceOf(AppError)
    expect(error.code).toBe('RATE_LIMITED')
    expect(error.correlationId).toBe('cid-1')
  })
})
