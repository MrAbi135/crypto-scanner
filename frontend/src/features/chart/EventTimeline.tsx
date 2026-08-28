// What the engine recorded on this window, in order (Blueprint §21.5).
//
// The chart shows where things are; this shows what happened and when. They
// answer different halves of "is the markup right", and the timeline is the
// half that has been missing -- a BOS has no shape on the price and is the
// event §3.5 spends its length defining.

import { useState } from 'react'

import type { StructureEvent } from '@entities/market/types'
import { flatten } from '@features/chart/inspection'
import { tally, timeline, type EventClass, type TimelineEntry } from '@features/chart/timeline'

const CLASSES: readonly { readonly id: EventClass; readonly label: string }[] = [
  { id: 'break', label: 'Breaks' },
  { id: 'shift', label: 'Shifts' },
  { id: 'label', label: 'Labels' },
  { id: 'other', label: 'Other' },
]

export interface EventTimelineProps {
  readonly events: readonly StructureEvent[]
}

export function EventTimeline({ events }: EventTimelineProps) {
  const entries = timeline(events)
  const counts = tally(entries)
  const [shown, setShown] = useState<EventClass | null>(null)

  const visible = shown === null ? entries : entries.filter((entry) => entry.kind === shown)

  if (entries.length === 0) {
    return (
      <aside className="timeline" data-testid="timeline-empty">
        <p>
          The engine recorded no breaks, shifts or labels on this window — only swings, which are
          drawn on the chart.
        </p>
      </aside>
    )
  }

  return (
    <aside className="timeline" data-testid="timeline">
      <header className="timeline__header">
        <h2 className="timeline__title">Recorded events</h2>

        <div className="chips__group" role="group" aria-label="Event class">
          <button
            type="button"
            className={`chip${shown === null ? ' chip--on' : ''}`}
            aria-pressed={shown === null}
            onClick={() => setShown(null)}
          >
            All {entries.length}
          </button>

          {CLASSES.filter(({ id }) => counts[id] > 0).map(({ id, label }) => (
            <button
              key={id}
              type="button"
              className={`chip${shown === id ? ' chip--on' : ''}`}
              aria-pressed={shown === id}
              data-testid={`timeline-class-${id}`}
              onClick={() => setShown(id)}
            >
              {label} {counts[id]}
            </button>
          ))}
        </div>
      </header>

      <ol className="timeline__list">
        {visible.map((entry) => (
          <Entry key={entry.key} entry={entry} />
        ))}
      </ol>
    </aside>
  )
}

function Entry({ entry }: { readonly entry: TimelineEntry }) {
  const [open, setOpen] = useState(false)
  const facts = flatten(entry.evidence)

  return (
    <li className={`timeline__entry timeline__entry--${entry.kind}`} data-testid={`event-${entry.key}`}>
      {/* `details` rather than a click handler on a div: it is a disclosure,
          the browser already knows how to announce and operate one, and it
          works before any JavaScript decides to. */}
      <details open={open} onToggle={(event) => setOpen(event.currentTarget.open)}>
        <summary>
          <time dateTime={entry.at}>{entry.at}</time>{' '}
          {/* The type verbatim, not a friendlier rewording. This screen is
              checked against the SLS, and a reader matching `BOS_UP` to §3.5
              cannot do it if the row says "Break upward". */}
          <span className="timeline__type">{entry.type}</span>
          {entry.direction !== null && (
            <span className="timeline__direction"> {entry.direction === 'UP' ? '▲' : '▼'}</span>
          )}
        </summary>

        {facts.length === 0 ? (
          <p className="timeline__none">The engine stored no evidence for this event.</p>
        ) : (
          <table className="evidence__table">
            <tbody>
              {facts.map((fact) => (
                <tr key={fact.label}>
                  <th scope="row">{fact.label}</th>
                  <td className="evidence__value">{fact.value}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </details>
    </li>
  )
}
