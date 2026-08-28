// S14's first screen: §18.4's live feed, ranked (Blueprint §21.4).
//
// The board only. Filters, presets, virtualization and the websocket are the
// rest of S14; this is the row that has to be right before any of them are
// worth adding, because every one of those either narrows it or re-renders it.

import { useCallback, useEffect, useState } from 'react'

import type { FeedRow, Meta } from '@entities/market/types'
import { ApiRequestError, fetchFeed } from '@services/api/client'
import { QuietFeed } from '@features/scanner/QuietFeed'
import { SignalRow } from '@features/scanner/SignalRow'

import './scanner.css'

interface Loaded {
  readonly rows: readonly FeedRow[]
  readonly liveTotal: number
  readonly meta: Meta
}

export interface ScannerScreenProps {
  /** Where "See the floors" goes. Supplied so this screen owns no routing. */
  readonly onShowFloors?: () => void
}

export function ScannerScreen({ onShowFloors }: ScannerScreenProps) {
  const [loaded, setLoaded] = useState<Loaded | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(true)

  const load = useCallback(async () => {
    setBusy(true)
    setError(null)

    try {
      const feed = await fetchFeed()

      setLoaded({
        rows: feed.data,
        // `?? 0` and not `?? feed.data.length`: the denominator is the
        // server's, and substituting the numerator for it would make a
        // filtered board claim nothing was hidden.
        liveTotal: feed.page?.live_total ?? 0,
        meta: feed.meta,
      })
    } catch (cause) {
      setError(
        cause instanceof ApiRequestError
          ? `${cause.code}: ${cause.message} (${cause.correlationId})`
          : 'The request failed.',
      )
    } finally {
      setBusy(false)
    }
  }, [])

  useEffect(() => {
    void load()
  }, [load])

  return (
    <section className="scanner">
      <header className="scanner__header">
        <h1 className="scanner__title">Live feed</h1>

        {busy && <span role="status">Loading…</span>}

        {loaded !== null && (
          // §13's freshness travels with the data, on screen. A board whose
          // age the reader cannot see is a board they have to trust.
          <span className="scanner__freshness" data-testid="freshness">
            {loaded.meta.freshness.state}
            {loaded.meta.freshness.observed_at !== undefined &&
              ` · newest ${loaded.meta.freshness.observed_at}`}
          </span>
        )}
      </header>

      {error !== null && (
        <p role="alert" data-testid="scanner-error">
          {error}
        </p>
      )}

      {loaded !== null && loaded.rows.length === 0 && error === null && (
        <QuietFeed
          liveTotal={loaded.liveTotal}
          // No filters in this piece, so the emptiness can only be the market.
          // Passed explicitly rather than defaulted: when filters land, the
          // compiler asks the question here.
          filtered={false}
          onShowFloors={onShowFloors ?? (() => undefined)}
          onClearFilter={() => undefined}
        />
      )}

      {loaded !== null && loaded.rows.length > 0 && (
        <table className="scanner__board" data-testid="feed-board">
          <caption className="scanner__caption">
            {loaded.rows.length} live signal{loaded.rows.length === 1 ? '' : 's'}, ranked by §9.2
          </caption>
          <thead>
            <tr>
              <th scope="col">#</th>
              <th scope="col">Symbol</th>
              <th scope="col">Direction</th>
              <th scope="col">Archetype</th>
              <th scope="col">Grade</th>
              <th scope="col">Confidence</th>
              <th scope="col">Entry / invalid / target</th>
              <th scope="col">Age</th>
            </tr>
          </thead>
          <tbody>
            {loaded.rows.map((row) => (
              <SignalRow key={row.signal_id} row={row} />
            ))}
          </tbody>
        </table>
      )}
    </section>
  )
}
