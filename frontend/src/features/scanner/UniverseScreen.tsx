// §18.4's universe view (Blueprint §21.4's universe stats, DDD T1/T2).
//
// The screen that answers "why is nothing ACTIVE". Every symbol on a young
// host reads INELIGIBLE/QUARANTINE with zero passes, which is what a stopped
// universe layer looks like too -- and was read as one. §1.4 puts two sevens
// in series and neither counter means anything without them, so the page
// carries both.

import { useCallback, useEffect, useState } from 'react'

import type { Meta, UniverseSymbol } from '@entities/market/types'
import { ApiRequestError, fetchUniverse } from '@services/api/client'

import './scanner.css'

const STATUSES = ['ACTIVE', 'QUARANTINE', 'DELISTED'] as const

interface Loaded {
  readonly rows: readonly UniverseSymbol[]
  readonly requiredObservations: number | null
  readonly requiredPromotions: number | null
  readonly hasMore: boolean
  readonly meta: Meta
}

export function UniverseScreen() {
  const [status, setStatus] = useState<string | null>(null)
  const [loaded, setLoaded] = useState<Loaded | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(true)

  const load = useCallback(async () => {
    setBusy(true)
    setError(null)

    try {
      const response = await fetchUniverse(status === null ? {} : { 'filter[status]': status })

      setLoaded({
        rows: response.data,
        requiredObservations: response.page?.required_observation_days ?? null,
        requiredPromotions: response.page?.required_promotion_days ?? null,
        hasMore: response.page?.has_more ?? false,
        meta: response.meta,
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
  }, [status])

  useEffect(() => {
    void load()
  }, [load])

  return (
    <section className="scanner">
      <header className="scanner__header">
        <h1 className="scanner__title">Universe</h1>
        {busy && <span role="status">Loading…</span>}
      </header>

      <div className="chips" data-testid="universe-filters">
        <fieldset className="chips__group">
          <legend className="chips__legend">Status</legend>

          <button
            type="button"
            className={`chip${status === null ? ' chip--on' : ''}`}
            aria-pressed={status === null}
            onClick={() => setStatus(null)}
          >
            All
          </button>

          {STATUSES.map((option) => (
            <button
              key={option}
              type="button"
              className={`chip${status === option ? ' chip--on' : ''}`}
              aria-pressed={status === option}
              data-testid={`universe-status-${option}`}
              onClick={() => setStatus(option)}
            >
              {option}
            </button>
          ))}
        </fieldset>
      </div>

      {error !== null && (
        <p role="alert" data-testid="universe-error">
          {error}
        </p>
      )}

      {loaded !== null && error === null && (
        <>
          {/* §1.4's thresholds, so a counter on the rows has a denominator.
              Absent rather than assumed: a page that printed "of 7" without
              being told 7 would be inventing the rule it is explaining. */}
          <p className="scanner__denominator" data-testid="universe-thresholds">
            {loaded.requiredObservations === null || loaded.requiredPromotions === null
              ? 'The page did not report §1.4’s thresholds.'
              : `§1.4: ${loaded.requiredObservations} daily observations before a symbol is ` +
                `evaluated at all, then ${loaded.requiredPromotions} consecutive passes to promote.`}
            {loaded.hasMore && ' Showing the first page.'}
          </p>

          {loaded.rows.length === 0 ? (
            <p className="quiet__reason" data-testid="universe-empty">
              No symbols with this status.
            </p>
          ) : (
            <table className="scanner__board" data-testid="universe-board">
              <caption className="scanner__caption">
                {loaded.rows.length} symbol{loaded.rows.length === 1 ? '' : 's'}
              </caption>
              <thead>
                <tr>
                  <th scope="col">Symbol</th>
                  <th scope="col">Status</th>
                  <th scope="col">Tier</th>
                  <th scope="col">Observations</th>
                  <th scope="col">Passes</th>
                  <th scope="col">Assessment</th>
                </tr>
              </thead>
              <tbody>
                {loaded.rows.map((row) => (
                  <tr key={row.symbol} data-testid={`universe-${row.symbol}`}>
                    <th scope="row">{row.symbol}</th>
                    <td>{row.status}</td>
                    <td>
                      {row.tier}
                      {row.candidate_tier !== null && ` → ${row.candidate_tier}`}
                    </td>
                    <td className="meter__value">
                      {row.observation_days}
                      {loaded.requiredObservations !== null && `/${loaded.requiredObservations}`}
                    </td>
                    <td className="meter__value">
                      {row.consecutive_passes}
                      {loaded.requiredPromotions !== null && `/${loaded.requiredPromotions}`}
                      {row.consecutive_failures > 0 && ` (${row.consecutive_failures} failed)`}
                    </td>
                    {/* The server's word. Deriving it here from the counters
                        would put §1.4's rule in two places, one of them
                        undocumented. */}
                    <td data-testid={`assessment-${row.symbol}`}>{row.assessment}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </>
      )}
    </section>
  )
}
