// Swing markers from what the engine recorded (SLS §3.1, §3.2).
//
// The structure endpoint returns raw events rather than a drawing model, on
// purpose: the chart must show what the engine decided, not a second opinion
// computed at read time. So the shaping happens here, and it is deliberately
// thin -- nothing in this file decides whether a swing exists.

import type { StructureEvent } from '@entities/market/types'

export type SwingKind = 'HIGH' | 'LOW'
export type SwingStrength = 'INTERNAL' | 'EXTERNAL'

export interface SwingMarker {
  /** Where the swing is, in time. See `at` below for why not the index. */
  readonly at: string
  readonly price: string
  readonly kind: SwingKind
  readonly strength: SwingStrength
  /** Stable within one response; the wire carries no swing id. */
  readonly key: string
}

const SWING = /^SWING_(INTERNAL|EXTERNAL)_(HIGH|LOW)$/

/**
 * Turn `SWING_*` events into markers, oldest first.
 *
 * **Positioned by `event_at` and never by the payload's `index`.** That index
 * is the swing's offset inside whichever five-hundred-candle window detected
 * it, frozen there while the window slides one candle per close. A chart that
 * placed a marker by it would drift a candle further left every hour and be
 * confidently wrong about the one thing the screen exists to verify. The same
 * trap has cost this project a pool map, a zone map, and five days of missing
 * breaks; here it would be visible, which is arguably worse.
 *
 * **Internal and external are both kept.** §3.1 says "every external swing is
 * by construction also an internal swing", and the engine persists the
 * promoted ones once, as external. Dropping the internal set would hide the
 * denser series the doctrine actually labels §3.3 against.
 *
 * Malformed rows are skipped rather than thrown on. This screen is the
 * verification instrument, and refusing to draw anything because one event in
 * a thousand has a missing price would take the instrument away exactly when
 * something is wrong with the data.
 */
export function swingMarkers(events: readonly StructureEvent[]): readonly SwingMarker[] {
  const markers: SwingMarker[] = []

  for (const event of events) {
    const match = SWING.exec(event.event_type)

    if (!match) continue

    const price = priceOf(event.evidence)

    if (price === null) continue

    markers.push({
      at: event.event_at,
      price,
      strength: match[1] as SwingStrength,
      kind: match[2] as SwingKind,
      key: `${event.event_type}-${event.event_at}`,
    })
  }

  return markers
}

function priceOf(evidence: unknown): string | null {
  if (typeof evidence !== 'object' || evidence === null) return null

  const price = (evidence as Record<string, unknown>).price

  // A string, because it stays one all the way to the screen (API §5). A
  // number here would mean the API sent one, which would itself be the bug.
  return typeof price === 'string' && price.length > 0 ? price : null
}
