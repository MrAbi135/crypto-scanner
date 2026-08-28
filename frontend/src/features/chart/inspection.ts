// What an overlay says about itself when you ask it (Roadmap S13a).
//
// The screen exists so the developer can look at a marked-up chart and judge
// whether the markup is right. That judgement needs the evidence the engine
// stored, so this turns each drawn object into something readable without
// turning it into something *else*.
//
// **Recorded facts, not a retelling.** Every value here is copied out of the
// object as the API sent it. Nothing is rounded, relabelled or computed. A
// panel that summarised would eventually disagree with the engine, and a
// disagreement between the instrument and the thing it measures is the one
// failure this screen cannot have -- it would be found, if ever, by trusting
// the instrument and being wrong.

import type { Pool, StructureEvent, Sweep, Zone } from '@entities/market/types'
import type { SweepMarker } from '@features/chart/sweeps'
import type { SwingMarker } from '@features/chart/swings'

export interface Fact {
  readonly label: string
  readonly value: string
}

export interface Inspection {
  /** Stable across re-renders of the same object, so focus can return to it. */
  readonly id: string
  readonly kind: 'zone' | 'pool' | 'sweep' | 'swing'
  readonly title: string
  /** The object's own columns -- identity, state, geometry. */
  readonly facts: readonly Fact[]
  /**
   * The `evidence` blob as stored, flattened one level.
   *
   * Kept separate from `facts` because the two have different standing: a
   * fact is a column the API contract names, and this is whatever the engine
   * happened to write. Merging them would let a payload key quietly shadow a
   * contract field.
   */
  readonly evidence: readonly Fact[]
}

export function inspectZone(zone: Zone): Inspection {
  return {
    id: `zone:${zone.zone_id}`,
    kind: 'zone',
    title: `${zone.zone_type} ${zone.grade} — ${zone.state}`,
    facts: [
      { label: 'zone id', value: zone.zone_id },
      { label: 'polarity', value: zone.polarity },
      { label: 'band', value: `${zone.band_low} – ${zone.band_high}` },
      ...(zone.refined_low !== null && zone.refined_high !== null
        ? [{ label: 'refined', value: `${zone.refined_low} – ${zone.refined_high}` }]
        : []),
      { label: 'created at', value: zone.created_at },
      { label: 'updated at', value: zone.updated_at },
      ...(zone.parent_zone_id !== null
        ? [{ label: 'parent zone', value: zone.parent_zone_id }]
        : []),
      // §8.2 G3 suspends a stale-context zone rather than deleting it, so a
      // reader has to be able to see that it is one.
      { label: 'stale context', value: String(zone.stale_context) },
      { label: 'gap adjacent', value: String(zone.gap_adjacent) },
    ],
    evidence: flatten(zone.evidence),
  }
}

export function inspectPool(pool: Pool): Inspection {
  return {
    id: `pool:${pool.pool_id}`,
    kind: 'pool',
    title: `${pool.side} ${pool.liquidity_class} pool — ${pool.state}`,
    facts: [
      { label: 'pool id', value: pool.pool_id },
      { label: 'price', value: pool.price },
      { label: 'band', value: `${pool.band_low} – ${pool.band_high}` },
      { label: 'source', value: pool.source },
      { label: 'members', value: String(pool.member_count) },
      // §4.2 scores strength from named components, and §15.2 says the score
      // travels with them. A bare number would be the thing §15.2 forbids.
      { label: 'strength', value: pool.strength.score },
    ],
    evidence: flatten(pool.strength.components),
  }
}

export function inspectSweep(sweep: Sweep, marker: SweepMarker): Inspection {
  return {
    id: `sweep:${marker.key}`,
    kind: 'sweep',
    title: `${marker.side} sweep — ${marker.reclaimed ? 'reclaimed' : 'held'}`,
    facts: [
      { label: 'pool id', value: marker.poolId },
      { label: 'level taken', value: marker.level },
      { label: 'reached', value: marker.penetration },
      ...(marker.depthAtr !== null ? [{ label: 'depth', value: `${marker.depthAtr} ATR` }] : []),
      // §4.6 calls a reclaimed sweep contrary evidence, so the panel says so
      // in words rather than leaving it to the dash pattern on the chart.
      {
        label: 'standing',
        value: marker.reclaimed ? 'reclaimed — contrary evidence (§4.6)' : 'unclaimed',
      },
      { label: 'transition', value: `${sweep.from_state} → ${sweep.to_state}` },
      { label: 'at', value: sweep.transitioned_at },
    ],
    evidence: flatten(sweep.evidence),
  }
}

export function inspectSwing(event: StructureEvent, marker: SwingMarker): Inspection {
  return {
    id: `swing:${marker.key}`,
    kind: 'swing',
    title: `${marker.strength} ${marker.kind} swing`,
    facts: [
      { label: 'price', value: marker.price },
      { label: 'at', value: marker.at },
      { label: 'event', value: event.event_type },
      // §15.2: the algo version is part of the evidence. Two swings from two
      // versions can disagree, and a reader comparing them needs to know.
      { label: 'algo version', value: event.algo_version },
    ],
    evidence: flatten(event.evidence),
  }
}

/**
 * One level of an evidence blob, as label/value pairs in key order.
 *
 * Flat and sorted rather than a recursive tree: the payloads are flat in
 * practice, a nested one renders as its JSON rather than silently losing its
 * inner keys, and sorted order means the same object reads the same way twice.
 * A tree component here would be more code standing between the reader and the
 * fact.
 */
export function flatten(evidence: unknown): readonly Fact[] {
  if (typeof evidence !== 'object' || evidence === null) return []

  return Object.entries(evidence as Record<string, unknown>)
    .map(([label, value]) => ({ label, value: render(value) }))
    .sort((a, b) => a.label.localeCompare(b.label))
}

function render(value: unknown): string {
  if (value === null) return 'null'
  if (typeof value === 'string') return value
  // Numbers and booleans arrive as themselves; a price would not, which is
  // why nothing here parses one back into a number.
  if (typeof value === 'number' || typeof value === 'boolean') return String(value)

  return JSON.stringify(value)
}
