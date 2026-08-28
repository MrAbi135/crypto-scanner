import { describe, expect, it } from 'vitest'

import type { StructureEvent } from '@entities/market/types'
import { swingMarkers } from './swings'

function event(
  event_type: string,
  event_at: string,
  evidence: unknown = { index: 42, price: '100.5', kind: 'HIGH', strength: 'EXTERNAL' },
): StructureEvent {
  return { event_type, event_at, algo_version: 's4-v8', evidence }
}

describe('swingMarkers', () => {
  it('reads price and classification off the recorded event', () => {
    const markers = swingMarkers([event('SWING_EXTERNAL_HIGH', '2026-08-17T01:00:00+00:00')])

    expect(markers).toHaveLength(1)
    expect(markers[0]).toMatchObject({
      at: '2026-08-17T01:00:00+00:00',
      price: '100.5',
      kind: 'HIGH',
      strength: 'EXTERNAL',
    })
  })

  it('carries no index, so nothing downstream can place a marker by one', () => {
    // The payload's `index` is the swing's offset inside whichever
    // five-hundred-candle window detected it, frozen there while the window
    // slides one candle per close. A marker placed by it would drift a candle
    // further left every hour -- confidently wrong about the one thing this
    // screen exists to verify. The marker type simply does not carry it, so a
    // later change cannot reach for it by accident.
    const markers = swingMarkers([event('SWING_INTERNAL_LOW', '2026-08-17T01:00:00+00:00')])

    const marker = markers[0]

    if (marker === undefined) throw new Error('expected one marker')

    expect(marker).not.toHaveProperty('index')
    expect(Object.keys(marker).sort()).toEqual(['at', 'key', 'kind', 'price', 'strength'])
  })

  it('keeps both strength series', () => {
    // §3.1: "every external swing is by construction also an internal swing".
    // Dropping the internal set would hide the denser series §3.3 labels
    // against.
    const markers = swingMarkers([
      event('SWING_INTERNAL_HIGH', '2026-08-17T01:00:00+00:00'),
      event('SWING_EXTERNAL_HIGH', '2026-08-17T02:00:00+00:00'),
    ])

    expect(markers.map((m) => m.strength)).toEqual(['INTERNAL', 'EXTERNAL'])
  })

  it('ignores structure events that are not swings', () => {
    // The endpoint returns everything the engine recorded on this window --
    // labels, breaks, sweeps. Taking the lot and trusting the regex is what
    // keeps a BOS from being drawn as a swing.
    const markers = swingMarkers([
      event('STRUCTURE_EXTERNAL_HH', '2026-08-17T01:00:00+00:00'),
      event('BOS_UP', '2026-08-17T02:00:00+00:00'),
      event('SWING_EXTERNAL_LOW', '2026-08-17T03:00:00+00:00'),
    ])

    expect(markers).toHaveLength(1)
    expect(markers[0]?.kind).toBe('LOW')
  })

  it('skips a malformed row rather than refusing the whole overlay', () => {
    // This screen is the verification instrument. Refusing to draw anything
    // because one event in a thousand lost its price would take the
    // instrument away at exactly the moment something is wrong with the data.
    const markers = swingMarkers([
      event('SWING_EXTERNAL_HIGH', '2026-08-17T01:00:00+00:00', { index: 1 }),
      event('SWING_EXTERNAL_HIGH', '2026-08-17T02:00:00+00:00', null),
      event('SWING_EXTERNAL_HIGH', '2026-08-17T03:00:00+00:00', { price: 100.5 }),
      event('SWING_EXTERNAL_HIGH', '2026-08-17T04:00:00+00:00'),
    ])

    expect(markers).toHaveLength(1)
    expect(markers[0]?.at).toBe('2026-08-17T04:00:00+00:00')
  })

  it('refuses a numeric price', () => {
    // A number here would mean the API sent one, and that is itself the bug
    // (API §5: prices are canonical decimal strings so they never pass through
    // IEEE-754). Drawing it would hide the defect behind a plausible marker.
    expect(
      swingMarkers([event('SWING_EXTERNAL_HIGH', '2026-08-17T01:00:00+00:00', { price: 100.5 })]),
    ).toHaveLength(0)
  })

  it('gives every marker a key that separates same-instant events', () => {
    // A high and a low can confirm on one candle, and React needs them to be
    // two rows rather than one.
    const markers = swingMarkers([
      event('SWING_EXTERNAL_HIGH', '2026-08-17T01:00:00+00:00'),
      event('SWING_EXTERNAL_LOW', '2026-08-17T01:00:00+00:00'),
    ])

    expect(new Set(markers.map((m) => m.key)).size).toBe(2)
  })
})
