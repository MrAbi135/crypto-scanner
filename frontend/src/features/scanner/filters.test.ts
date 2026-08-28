import { describe, expect, it } from 'vitest'

import { describe as describeSelection, isFiltered, toQuery, toggle } from './filters'

describe('toQuery', () => {
  it('uses the plain operator for one value', () => {
    // A request should say what it means; `[in]` with one member reads as a
    // list that happens to be short.
    expect(toQuery({ grade: ['B'] })).toEqual({ 'filter[grade]': 'B' })
  })

  it('uses [in] for several', () => {
    expect(toQuery({ grade: ['S', 'A'] })).toEqual({ 'filter[grade][in]': 'S,A' })
  })

  it('sends nothing for a field with nothing selected', () => {
    expect(toQuery({})).toEqual({})
    expect(toQuery({ grade: [] })).toEqual({})
  })

  it('sends each field separately, because fields AND', () => {
    expect(toQuery({ grade: ['B'], direction: ['UP'] })).toEqual({
      'filter[grade]': 'B',
      'filter[direction]': 'UP',
    })
  })
})

describe('toggle', () => {
  it('drops the key when the last value comes off', () => {
    // Not left as an empty array. `isFiltered` reads presence, so an empty one
    // would report a filter that is switched off -- and the quiet feed would
    // then tell the user their own chip hid a market that is simply silent.
    const on = toggle({}, 'grade', 'B')
    const off = toggle(on, 'grade', 'B')

    expect(Object.hasOwn(off, 'grade')).toBe(false)
    expect(isFiltered(off)).toBe(false)
  })

  it('leaves the other fields alone', () => {
    const both = toggle(toggle({}, 'grade', 'B'), 'direction', 'UP')

    expect(toggle(both, 'grade', 'B')).toEqual({ direction: ['UP'] })
  })

  it('adds rather than replaces within one field', () => {
    expect(toggle(toggle({}, 'grade', 'B'), 'grade', 'A')).toEqual({ grade: ['B', 'A'] })
  })
})

describe('describe', () => {
  it('names what is on, so the empty state can state it', () => {
    expect(describeSelection({ grade: ['S', 'A'], direction: ['UP'] })).toBe(
      'grade S or A, direction UP',
    )
  })

  it('is empty when nothing is on', () => {
    expect(describeSelection({})).toBe('')
  })
})
