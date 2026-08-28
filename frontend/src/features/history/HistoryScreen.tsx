// S16's honesty archive — the track record (Blueprint §21.6, PRD FC-10.1).
//
// The screen where the platform's claim about itself can be audited, which
// fixes its posture: it renders the record's own words and adds none of its
// own. The hit-rate labels ("n=14 — insufficient") come from the payload, the
// exclusions are shown rather than absorbed, and a rate the server refused to
// state (null, nothing rated) renders as unstated — never as 0%, which would
// be a claim from no evidence.

import { useCallback, useEffect, useState } from 'react'

import type { ArchivedSignal, StatsGroup } from '@entities/market/types'
import { ApiRequestError, fetchHistory, fetchStatistics } from '@services/api/client'

import './history.css'

const OUTCOMES = ['SUCCESS', 'FAILED', 'EXPIRED_UNTOUCHED', 'INVALIDATED_EARLY'] as const
const WINDOWS = ['7d', '30d', '90d', '365d', 'all'] as const
const AXES = ['archetype', 'grade', 'timeframe', 'symbol'] as const

export interface HistoryScreenProps {
  /** Open one archived signal's detail. */
  readonly onOpenSignal?: ((signalId: string) => void) | undefined
}

type Slot<T> = { kind: 'loading' } | { kind: 'error'; message: string } | { kind: 'ready'; value: T }

function describe(cause: unknown): string {
  return cause instanceof ApiRequestError
    ? `${cause.code}: ${cause.message} (${cause.correlationId})`
    : 'The request failed.'
}

export function HistoryScreen({ onOpenSignal }: HistoryScreenProps) {
  const [outcome, setOutcome] = useState<string | null>(null)
  const [window, setWindow] = useState<(typeof WINDOWS)[number]>('all')
  const [axis, setAxis] = useState<(typeof AXES)[number]>('archetype')
  const [rows, setRows] = useState<Slot<readonly ArchivedSignal[]>>({ kind: 'loading' })
  const [stats, setStats] = useState<Slot<readonly StatsGroup[]>>({ kind: 'loading' })

  const loadRows = useCallback(async () => {
    setRows({ kind: 'loading' })

    try {
      const response = await fetchHistory(
        outcome === null ? {} : { 'filter[outcome]': outcome },
      )

      setRows({ kind: 'ready', value: response.data })
    } catch (cause) {
      setRows({ kind: 'error', message: describe(cause) })
    }
  }, [outcome])

  const loadStats = useCallback(async () => {
    setStats({ kind: 'loading' })

    try {
      const response = await fetchStatistics(axis, window)

      setStats({ kind: 'ready', value: response.data })
    } catch (cause) {
      setStats({ kind: 'error', message: describe(cause) })
    }
  }, [axis, window])

  useEffect(() => {
    void loadRows()
  }, [loadRows])

  useEffect(() => {
    void loadStats()
  }, [loadStats])

  return (
    <section className="history">
      <header>
        <h1 className="history__title">Track record</h1>
        <p className="history__strap">
          The whole record, uncurated. Free tier included — §18.8: honesty is never paywalled.
        </p>
      </header>

      <section aria-label="Statistics">
        <div className="chips">
          <fieldset className="chips__group">
            <legend className="chips__legend">Window</legend>
            {WINDOWS.map((option) => (
              <button
                key={option}
                type="button"
                className={`chip${window === option ? ' chip--on' : ''}`}
                aria-pressed={window === option}
                data-testid={`window-${option}`}
                onClick={() => setWindow(option)}
              >
                {option}
              </button>
            ))}
          </fieldset>

          <fieldset className="chips__group">
            <legend className="chips__legend">Group by</legend>
            {AXES.map((option) => (
              <button
                key={option}
                type="button"
                className={`chip${axis === option ? ' chip--on' : ''}`}
                aria-pressed={axis === option}
                data-testid={`axis-${option}`}
                onClick={() => setAxis(option)}
              >
                {option}
              </button>
            ))}
          </fieldset>
        </div>

        {stats.kind === 'loading' && <p role="status">Loading statistics…</p>}

        {stats.kind === 'error' && (
          <p role="alert" data-testid="stats-error">
            The statistics could not be read: {stats.message}
          </p>
        )}

        {stats.kind === 'ready' &&
          (stats.value.length === 0 ? (
            <p data-testid="stats-empty">Nothing resolved in this window yet.</p>
          ) : (
            <ul className="history__stats" data-testid="stats-groups">
              {stats.value.map((group) => (
                <StatCard key={`${group.key ?? 'version'}-${group.algo_version}`} group={group} />
              ))}
            </ul>
          ))}
      </section>

      <section aria-label="Archive">
        <div className="chips" data-testid="history-filters">
          <fieldset className="chips__group">
            <legend className="chips__legend">Outcome</legend>
            <button
              type="button"
              className={`chip${outcome === null ? ' chip--on' : ''}`}
              aria-pressed={outcome === null}
              onClick={() => setOutcome(null)}
            >
              All
            </button>
            {OUTCOMES.map((option) => (
              <button
                key={option}
                type="button"
                className={`chip${outcome === option ? ' chip--on' : ''}`}
                aria-pressed={outcome === option}
                data-testid={`outcome-${option}`}
                onClick={() => setOutcome(option)}
              >
                {option}
              </button>
            ))}
          </fieldset>
        </div>

        {rows.kind === 'loading' && <p role="status">Loading the archive…</p>}

        {rows.kind === 'error' && (
          <p role="alert" data-testid="history-error">
            {rows.message}
          </p>
        )}

        {rows.kind === 'ready' &&
          (rows.value.length === 0 ? (
            <p data-testid="history-empty">
              No archived signals match. A young platform's archive is small; that is the
              record, not a failure to load it.
            </p>
          ) : (
            <table className="history__table" data-testid="history-table">
              <caption className="history__caption">
                {rows.value.length} archived signal{rows.value.length === 1 ? '' : 's'}, newest
                first
              </caption>
              <thead>
                <tr>
                  <th scope="col">Signal</th>
                  <th scope="col">Published</th>
                  <th scope="col">Grade</th>
                  <th scope="col">Confidence</th>
                  <th scope="col">Outcome</th>
                  <th scope="col">MFE / MAE</th>
                </tr>
              </thead>
              <tbody>
                {rows.value.map((row) => (
                  <tr key={row.signal_id} data-testid={`archived-${row.signal_id}`}>
                    <th scope="row">
                      {onOpenSignal === undefined ? (
                        `${row.symbol} ${row.timeframe} ${row.archetype}`
                      ) : (
                        <button
                          type="button"
                          className="history__link"
                          aria-label={`Open the ${row.symbol} ${row.timeframe} signal detail`}
                          data-testid={`open-archived-${row.signal_id}`}
                          onClick={() => onOpenSignal(row.signal_id)}
                        >
                          {row.symbol} {row.timeframe} {row.archetype}
                        </button>
                      )}
                    </th>
                    <td className="history__mono">{row.published_at}</td>
                    <td>{row.grade}</td>
                    <td className="history__mono">{row.confidence}</td>
                    <td data-testid={`outcome-of-${row.signal_id}`}>
                      {row.outcome === undefined ? (
                        'live'
                      ) : (
                        <>
                          {row.outcome.outcome}
                          {/* PRD FC-10.1: present in the archive, out of the
                              stats — and the reader is told which rows the
                              statistics above are not counting. */}
                          {row.outcome.excluded_from_stats && (
                            <em data-testid={`excluded-${row.signal_id}`}>
                              {' '}
                              (excluded from stats)
                            </em>
                          )}
                        </>
                      )}
                    </td>
                    <td className="history__mono">
                      {row.outcome === undefined || row.outcome.mfe_r === null
                        ? '—'
                        : `${row.outcome.mfe_r}R / ${row.outcome.mae_r ?? '—'}R`}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          ))}
      </section>
    </section>
  )
}

/** C15: one group's record, small-sample honesty built in. */
function StatCard({ group }: { readonly group: StatsGroup }) {
  const rate = group.hit_rate

  return (
    <li className="history__card" data-testid={`stats-${group.key ?? group.algo_version}`}>
      <h2 className="history__card-title">
        {group.key ?? group.algo_version}
        {/* §18.8: version-segmented always. A hit rate averaged across two
            algo versions is the average of two different scanners. */}
        {group.key !== null && <span className="history__version"> · {group.algo_version}</span>}
      </h2>

      <p className="history__rate">
        {rate.rate_pct === null ? (
          // Null, never zero: zero is a claim from no evidence.
          <span data-testid="rate-unstated">no rated outcomes yet</span>
        ) : (
          <>
            <strong>{rate.rate_pct}%</strong> hit rate over {rate.rated} rated
            {rate.confidence_interval !== null &&
              ` (${rate.confidence_interval.level}: ${rate.confidence_interval.low_pct}–${rate.confidence_interval.high_pct}%)`}
          </>
        )}
      </p>

      {/* The server's own phrasing. PRD FC-10.1 wants "n=14 — insufficient"
          honesty in the stat primitive itself, so the label ships in the
          payload and this renders it verbatim. */}
      {!rate.sufficient_for_inference && (
        <p className="history__label" data-testid="small-sample">
          {rate.label}
        </p>
      )}

      <p className="history__counts">
        {group.counts.success} success · {group.counts.failed} failed · {group.counts.expired}{' '}
        expired · {group.counts.invalidated_early} invalidated early
      </p>
    </li>
  )
}
