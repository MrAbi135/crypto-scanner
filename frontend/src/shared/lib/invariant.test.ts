import { describe, expect, it } from 'vitest'
import { invariant } from './invariant'

describe('invariant', () => {
  it('passes on a truthy condition', () => {
    expect(() => invariant(true, 'ok')).not.toThrow()
  })

  it('throws on a falsy condition outside production', () => {
    expect(() => invariant(false, 'bad')).toThrow(/Invariant failed: bad/)
  })
})
