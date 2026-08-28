// Sweep markers from the pool transitions the engine recorded (SLS §4.6).
//
// A sweep is not a point. §4.6 defines it as price penetrating a resting level
// and then closing back on the other side, so what the chart has to show is
// the *reach* -- from the level to the far edge of the penetration. Drawn as a
// dot at the level it would be indistinguishable from a touch, which is the
// one thing §4.6 spends its length separating it from.

import type { Sweep } from '@entities/market/types'

export type SweepSide = 'BSL' | 'SSL'

export interface SweepMarker {
  /** When it confirmed. See `at` in `swings.ts` for why not the index. */
  readonly at: string
  /** The resting level that was taken. */
  readonly level: string
  /** How far beyond the level price reached. */
  readonly penetration: string
  readonly side: SweepSide
  /**
   * §4.6 calls a reclaimed sweep "contrary evidence" -- the level was taken
   * and then given back, so it argues against the setup rather than for it.
   * Carried so the chart can say which, because the two look identical in
   * geometry and mean opposite things.
   */
  readonly reclaimed: boolean
  readonly depthAtr: string | null
  readonly poolId: string
  readonly key: string
}

/**
 * Shape the transitions into drawable sweeps, oldest first.
 *
 * **Filtered on `reason` here even though the endpoint already filters.** The
 * chart's meaning of "sweep overlay" should not depend on a decision made in
 * a router it does not own: a pool goes SWEPT, BROKEN and EXPIRED, and a
 * BROKEN transition rendered as a sweep would be a confident lie on the
 * instrument used to verify the doctrine.
 *
 * **Positioned by `transitioned_at`, never by `candle_index`.** That index is
 * the offset inside whichever window recorded the transition, frozen while the
 * window slides. `Sweep` carries it on the wire and `SweepMarker` does not, so
 * nothing downstream can reach for it.
 *
 * A transition missing either price is skipped rather than half-drawn. A
 * segment with one end guessed is worse than an absent one: it looks measured.
 */
/** The marker key for a transition. See `swingKey` for why it is a function. */
export function sweepKey(sweep: Sweep): string {
  return `${sweep.pool_id}-${sweep.transitioned_at}`
}

export function sweepMarkers(sweeps: readonly Sweep[]): readonly SweepMarker[] {
  const markers: SweepMarker[] = []

  for (const sweep of sweeps) {
    if (sweep.reason !== 'liquidity_sweep') continue

    const evidence = sweep.evidence

    if (typeof evidence !== 'object' || evidence === null) continue

    const row = evidence as Record<string, unknown>

    const level = decimal(row.reference_level)
    const penetration = decimal(row.penetration_price)
    const side = row.side

    if (level === null || penetration === null) continue
    if (side !== 'BSL' && side !== 'SSL') continue

    markers.push({
      at: sweep.transitioned_at,
      level,
      penetration,
      side,
      // Absent is not the same as false, but §4.6's award is withheld unless
      // the engine positively says unclaimed, so absent is treated as the
      // cautious answer rather than the flattering one.
      reclaimed: row.reclaimed !== false,
      depthAtr: decimal(row.sweep_depth_atr),
      poolId: sweep.pool_id,
      key: sweepKey(sweep),
    })
  }

  return markers
}

function decimal(value: unknown): string | null {
  // A string, because prices stay strings all the way to the screen (API §5).
  // A number arriving here would mean the API sent one, which is the defect.
  return typeof value === 'string' && value.length > 0 ? value : null
}
