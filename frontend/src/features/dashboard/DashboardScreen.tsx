// S16's hub (Blueprint §21.6), restricted to what is measurable.
//
// §21.6 lists six panels. Three are served — top signals, recent sweeps, and
// the data status strip that already sits above every view — and the other
// three are *named on the screen* by the server's own `not_measured` list,
// the same contract the status strip established: a hub that quietly shows
// half its panels reads as a hub that checked the rest and found them quiet.

import { useEffect, useState } from 'react'

import type { DashboardOverview } from '@entities/market/types'
import { ApiRequestError, fetchOverview } from '@services/api/client'

import './dashboard.css'

export interface DashboardScreenProps {
  readonly onOpenSignal?: ((signalId: string) => void) | undefined
  readonly onOpenChart?: ((symbol: string, timeframe: string) => void) | undefined
}

type State =
  | { readonly kind: 'loading' }
  | { readonly kind: 'error'; readonly message: string }
  | { readonly kind: 'ready'; readonly overview: DashboardOverview }

export function DashboardScreen({ onOpenSignal, onOpenChart }: DashboardScreenProps) {
  const [state, setState] = useState<State>({ kind: 'loading' })

  useEffect(() => {
    let cancelled = false

    fetchOverview()
      .then((response) => {
        if (!cancelled) setState({ kind: 'ready', overview: response.data })
      })
      .catch((cause) => {
        if (!cancelled) {
          setState({
            kind: 'error',
            message:
              cause instanceof ApiRequestError
                ? `${cause.code}: ${cause.message} (${cause.correlationId})`
                : 'The request failed.',
          })
        }
      })

    return () => {
      cancelled = true
    }
  }, [])

  if (state.kind === 'loading') {
    return (
      <section className="dashboard">
        <p role="status">Loading the dashboard…</p>
      </section>
    )
  }

  if (state.kind === 'error') {
    return (
      <section className="dashboard">
        <p role="alert" data-testid="dashboard-error">
          {state.message}
        </p>
      </section>
    )
  }

  const { overview } = state

  return (
    <section className="dashboard" data-testid="dashboard">
      <h1 className="dashboard__title">Dashboard</h1>

      <section aria-label="Top signals">
        <h2 className="dashboard__subtitle">
          Top signals
          {/* The denominator, for the feed's own reason: five of five and five
              of ninety are different markets. */}
          <span className="dashboard__count" data-testid="dashboard-live-total">
            {' '}
            — {overview.top_signals.length} of {overview.live_total} live
          </span>
        </h2>

        {overview.top_signals.length === 0 ? (
          <p data-testid="dashboard-quiet">No live signals. A quiet market is a real answer.</p>
        ) : (
          <ol className="dashboard__signals" data-testid="dashboard-signals">
            {overview.top_signals.map((row) => (
              <li key={row.signal_id}>
                {onOpenSignal === undefined ? (
                  <span>
                    {row.symbol} {row.timeframe} {row.archetype} {row.grade}
                  </span>
                ) : (
                  <button
                    type="button"
                    className="dashboard__link"
                    data-testid={`dashboard-open-${row.signal_id}`}
                    aria-label={`Open the ${row.symbol} ${row.timeframe} signal detail`}
                    onClick={() => onOpenSignal(row.signal_id)}
                  >
                    {row.symbol} {row.timeframe} {row.archetype} {row.grade}
                  </button>
                )}{' '}
                <span className="dashboard__mono">
                  {row.direction === 'UP' ? 'Long' : 'Short'} · rank {row.display_rank} ·{' '}
                  {row.lifecycle_state}
                </span>
              </li>
            ))}
          </ol>
        )}
      </section>

      <section aria-label="Recent sweeps">
        <h2 className="dashboard__subtitle">Recent sweeps</h2>

        {overview.recent_sweeps.length === 0 ? (
          <p data-testid="dashboard-no-sweeps">No levels consumed recently.</p>
        ) : (
          <ul className="dashboard__sweeps" data-testid="dashboard-sweeps">
            {overview.recent_sweeps.map((sweep) => (
              <li key={`${sweep.pool_id}-${sweep.at}`}>
                {onOpenChart === undefined ? (
                  <span>
                    {sweep.symbol} {sweep.timeframe}
                  </span>
                ) : (
                  <button
                    type="button"
                    className="dashboard__link"
                    aria-label={`Open ${sweep.symbol} ${sweep.timeframe} on the chart`}
                    onClick={() => onOpenChart(sweep.symbol, sweep.timeframe)}
                  >
                    {sweep.symbol} {sweep.timeframe}
                  </button>
                )}{' '}
                {/* Side can be null when the pool row is gone -- the record
                    outlives the object, and "side unknown" is the truth. */}
                <span className="dashboard__mono">
                  {sweep.side ?? 'side unknown'} · {sweep.event} · {sweep.reason} · {sweep.at}
                </span>
              </li>
            ))}
          </ul>
        )}
      </section>

      <p className="dashboard__gap" data-testid="dashboard-not-measured">
        Not measured here: {overview.not_measured.join('; ')}.
      </p>
    </section>
  )
}
