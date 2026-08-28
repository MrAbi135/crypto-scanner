import { describe, expect, it } from 'vitest'

import type { StructureEvent } from '@entities/market/types'
import { classify, tally, timeline } from './timeline'

function event(event_type: string, event_at = '2026-08-17T01:00:00+00:00'): StructureEvent {
  return { event_type, event_at, algo_version: 's4-v8', evidence: { note: 1 } }
}

describe('classify', () => {
  it('separates the families the doctrine separates', () => {
    expect(classify('BOS_UP')).toBe('break')
    expect(classify('STRUCTURE_FAILED_BREAK_UP')).toBe('break')
    expect(classify('MSS_DOWN')).toBe('shift')
    expect(classify('CHOCH_UP')).toBe('shift')
    expect(classify('STRUCTURE_EXTERNAL_HH')).toBe('label')
    expect(classify('SWING_EXTERNAL_HIGH')).toBe('swing')
  })

  it('admits it does not know, rather than guessing', () => {
    // The engine gains detectors. Silently dropping an unknown type would make
    // a new one invisible on the instrument built to watch detectors.
    expect(classify('SOMETHING_NEW')).toBe('other')
  })

  it('puts a failed break with the breaks and not with the labels', () => {
    // It starts with `STRUCTURE_`, so prefix order matters: checked before the
    // generic label rule or it lands in the wrong family.
    expect(classify('STRUCTURE_FAILED_BREAK_DOWN')).toBe('break')
  })
})

describe('timeline', () => {
  it('leaves the swings to the chart', () => {
    // The swing series is the densest thing the endpoint returns; repeating it
    // here would bury eleven breaks under two hundred pivots.
    const entries = timeline([event('SWING_EXTERNAL_HIGH'), event('BOS_UP')])

    expect(entries.map((e) => e.type)).toEqual(['BOS_UP'])
  })

  it('is newest first', () => {
    const entries = timeline([
      event('BOS_UP', '2026-08-17T01:00:00+00:00'),
      event('MSS_UP', '2026-08-17T05:00:00+00:00'),
    ])

    expect(entries.map((e) => e.type)).toEqual(['MSS_UP', 'BOS_UP'])
  })

  it('reads the direction off the type', () => {
    expect(timeline([event('BOS_DOWN')])[0]?.direction).toBe('DOWN')
    expect(timeline([event('STRUCTURE_EXTERNAL_HH')])[0]?.direction).toBeNull()
  })

  it('keeps an unknown type rather than dropping it', () => {
    expect(timeline([event('SOMETHING_NEW')]).map((e) => e.kind)).toEqual(['other'])
  })
})

describe('tally', () => {
  it('counts each family', () => {
    const counts = tally(timeline([event('BOS_UP'), event('BOS_DOWN'), event('MSS_UP')]))

    expect(counts.break).toBe(2)
    expect(counts.shift).toBe(1)
    expect(counts.label).toBe(0)
  })
})
