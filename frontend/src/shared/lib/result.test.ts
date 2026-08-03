import { describe, expect, it } from 'vitest'
import { err, map, ok, unwrapOr } from './result'

describe('result', () => {
  it('maps an ok value', () => {
    expect(map(ok(2), (v) => v + 1)).toEqual(ok(3))
  })

  it('map is a no-op on err', () => {
    expect(map(err('boom'), (v: number) => v + 1)).toEqual(err('boom'))
  })

  it('unwrapOr returns value or fallback', () => {
    expect(unwrapOr(ok(2), 9)).toBe(2)
    expect(unwrapOr(err('boom'), 9)).toBe(9)
  })
})
