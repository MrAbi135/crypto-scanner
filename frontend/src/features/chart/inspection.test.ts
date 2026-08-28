import { describe, expect, it } from 'vitest'

import type { Pool, StructureEvent, Sweep, Zone } from '@entities/market/types'
import { flatten, inspectPool, inspectSweep, inspectSwing, inspectZone } from './inspection'
import { sweepMarkers } from './sweeps'
import { swingMarkers } from './swings'

function zone(overrides: Partial<Zone> = {}): Zone {
  return {
    zone_id: 'z1',
    zone_type: 'OB',
    polarity: 'BULLISH',
    state: 'FRESH',
    grade: 'OB_A',
    band_low: '100.10',
    band_high: '101.20',
    refined_low: null,
    refined_high: null,
    created_at: '2026-08-17T01:00:00+00:00',
    created_index: 400,
    confirmed_index: 402,
    parent_zone_id: null,
    stale_context: false,
    gap_adjacent: false,
    updated_at: '2026-08-17T02:00:00+00:00',
    evidence: { mss_origin: true, external_structure_break: false },
    ...overrides,
  }
}

describe('flatten', () => {
  it('reads a payload back in a stable order', () => {
    // The same object must read the same way twice, or two inspections of one
    // zone look like two different zones.
    const facts = flatten({ zebra: 1, alpha: 2 })

    expect(facts.map((f) => f.label)).toEqual(['alpha', 'zebra'])
  })

  it('shows a decimal string exactly as it arrived', () => {
    // Prices are canonical decimal strings so they never pass through
    // IEEE-754 (API §5). Reformatting one here would undo that at the last
    // step, on the screen whose job is to be exact.
    expect(flatten({ price: '62754.000000000000000000' })[0]?.value).toBe(
      '62754.000000000000000000',
    )
  })

  it('renders a nested value rather than dropping it', () => {
    const [fact] = flatten({ components: { touches: 3 } })

    expect(fact?.value).toBe('{"touches":3}')
  })

  it('distinguishes a null from a missing key', () => {
    expect(flatten({ resolution: null })).toEqual([{ label: 'resolution', value: 'null' }])
    expect(flatten({})).toEqual([])
  })

  it('survives a payload that is not an object', () => {
    expect(flatten(null)).toEqual([])
    expect(flatten('nonsense')).toEqual([])
  })
})

describe('inspectZone', () => {
  it('keeps the evidence payload out of the contract facts', () => {
    // A payload key must not be able to shadow a column the API contract
    // names -- the two have different standing and a merged table would hide
    // which is which.
    const view = inspectZone(zone({ evidence: { state: 'INVENTED' } }))

    expect(view.facts.find((f) => f.label === 'state')).toBeUndefined()
    expect(view.evidence).toEqual([{ label: 'state', value: 'INVENTED' }])
  })

  it('surfaces stale context, which suspends the zone rather than deleting it', () => {
    const view = inspectZone(zone({ stale_context: true }))

    expect(view.facts).toContainEqual({ label: 'stale context', value: 'true' })
  })

  it('omits the refined band when the engine recorded none', () => {
    // Rather than printing "null – null", which reads as a measured band.
    expect(inspectZone(zone()).facts.map((f) => f.label)).not.toContain('refined')
  })
})

describe('inspectPool', () => {
  it('carries the strength components, not just the score', () => {
    // §15.2: the score travels with the components that produced it. A bare
    // number is the thing §15.2 forbids.
    const pool: Pool = {
      pool_id: 'p1',
      side: 'BSL',
      liquidity_class: 'EXTERNAL',
      source: 'swing',
      price: '105',
      band_low: '104',
      band_high: '106',
      state: 'ACTIVE',
      member_count: 2,
      created_index: 400,
      strength: { score: '23.75', components: { touches: 2, age: 30 } },
    }

    const view = inspectPool(pool)

    expect(view.facts).toContainEqual({ label: 'strength', value: '23.75' })
    expect(view.evidence.map((f) => f.label)).toEqual(['age', 'touches'])
  })
})

describe('inspectSweep', () => {
  function sweep(evidence: Record<string, unknown> = {}): Sweep {
    return {
      pool_id: 'p1',
      from_state: 'ACTIVE',
      to_state: 'SWEPT',
      reason: 'liquidity_sweep',
      transitioned_at: '2026-08-17T01:00:00+00:00',
      candle_index: 400,
      evidence: {
        reference_level: '105',
        penetration_price: '110',
        side: 'BSL',
        reclaimed: false,
        sweep_depth_atr: '0.5',
        ...evidence,
      },
    }
  }

  it('states in words that a reclaimed sweep argues against the setup', () => {
    // §4.6 calls it contrary evidence. On the chart that is a dash pattern;
    // here it has to be sayable, because a reader checking a label needs the
    // reason and not only the mark.
    const row = sweep({ reclaimed: true })
    const marker = sweepMarkers([row])[0]

    if (marker === undefined) throw new Error('expected a marker')

    const view = inspectSweep(row, marker)

    expect(view.facts).toContainEqual({
      label: 'standing',
      value: 'reclaimed — contrary evidence (§4.6)',
    })
  })

  it('shows the transition it came from', () => {
    const row = sweep()
    const marker = sweepMarkers([row])[0]

    if (marker === undefined) throw new Error('expected a marker')

    expect(inspectSweep(row, marker).facts).toContainEqual({
      label: 'transition',
      value: 'ACTIVE → SWEPT',
    })
  })
})

describe('inspectSwing', () => {
  it('carries the algo version that produced it', () => {
    // §15.2 makes the version part of the evidence: two swings from two
    // versions can disagree, and a reader comparing them needs to know which
    // is which.
    const event: StructureEvent = {
      event_type: 'SWING_EXTERNAL_HIGH',
      event_at: '2026-08-17T01:00:00+00:00',
      algo_version: 's4-v8',
      evidence: { price: '110', index: 400 },
    }

    const marker = swingMarkers([event])[0]

    if (marker === undefined) throw new Error('expected a marker')

    const view = inspectSwing(event, marker)

    expect(view.facts).toContainEqual({ label: 'algo version', value: 's4-v8' })
    // The window-local index is not hidden -- it is part of what the engine
    // stored, and the panel shows what was stored. It is simply never used to
    // place anything.
    expect(view.evidence).toContainEqual({ label: 'index', value: '400' })
  })
})
