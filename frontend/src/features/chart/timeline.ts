// The structure events the chart does not draw (Blueprint §21.5's event
// timeline).
//
// `/coins/{id}/structure` returns everything the engine recorded on the window
// -- swings, labels, breaks, shifts, failed breaks -- and the chart draws the
// swings. The rest was fetched and discarded, which for the screen that exists
// to verify the doctrine is the wrong half to throw away: a BOS is the event
// §3.5 spends its length defining, and until now it reached the browser and
// vanished.

import type { StructureEvent } from '@entities/market/types'

export type EventClass = 'break' | 'shift' | 'label' | 'swing' | 'other'

export interface TimelineEntry {
  readonly at: string
  readonly type: string
  /** What the row is, for grouping and for colour that is not the only signal. */
  readonly kind: EventClass
  /** UP / DOWN where the event has one; null where it does not. */
  readonly direction: 'UP' | 'DOWN' | null
  readonly evidence: unknown
  readonly key: string
}

/**
 * Classify by prefix, and say `other` rather than guessing.
 *
 * An unknown event type is a real possibility -- the engine gains detectors --
 * and the honest rendering of one is a row that says its name and admits the
 * screen does not know what family it belongs to. Silently dropping it would
 * make a new detector invisible on the instrument built to watch detectors.
 */
export function classify(eventType: string): EventClass {
  if (eventType.startsWith('BOS_')) return 'break'
  if (eventType.startsWith('STRUCTURE_FAILED_BREAK_')) return 'break'
  if (eventType.startsWith('MSS_') || eventType.startsWith('CHOCH_')) return 'shift'
  if (eventType.startsWith('SWING_')) return 'swing'
  if (eventType.startsWith('STRUCTURE_')) return 'label'

  return 'other'
}

function directionOf(eventType: string): 'UP' | 'DOWN' | null {
  if (eventType.endsWith('_UP')) return 'UP'
  if (eventType.endsWith('_DOWN')) return 'DOWN'

  return null
}

/**
 * Newest first, swings excluded.
 *
 * Excluded because they are already on the chart as markers, and a timeline
 * that repeated them would bury the eleven breaks under two hundred pivots --
 * the swing series is the densest thing the endpoint returns. They remain
 * classifiable so a caller that wants them can ask.
 */
export function timeline(events: readonly StructureEvent[]): readonly TimelineEntry[] {
  return events
    .map((event) => ({
      at: event.event_at,
      type: event.event_type,
      kind: classify(event.event_type),
      direction: directionOf(event.event_type),
      evidence: event.evidence,
      key: `${event.event_type}-${event.event_at}`,
    }))
    .filter((entry) => entry.kind !== 'swing')
    .sort((a, b) => b.at.localeCompare(a.at))
}

/** How many of each class, for a header that says what the window held. */
export function tally(entries: readonly TimelineEntry[]): Record<EventClass, number> {
  const counts: Record<EventClass, number> = {
    break: 0,
    shift: 0,
    label: 0,
    swing: 0,
    other: 0,
  }

  for (const entry of entries) counts[entry.kind] += 1

  return counts
}
