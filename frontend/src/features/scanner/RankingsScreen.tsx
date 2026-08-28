// §18.6's deterministic board, with §9.1's weights beside it (Blueprint
// §21.8).
//
// Different question from the feed, and the screens say so. The feed answers
// what is on the table now, across everything. This answers what one timeframe
// offered at one close -- including the candidates that scored and did not
// publish, which is the number that separates a quiet market from a broken
// pipeline.

import { useCallback, useEffect, useState } from 'react'

import type { Meta, RankedRow, Weights } from '@entities/market/types'
import { ApiRequestError, fetchRankings, fetchWeights } from '@services/api/client'
import { WeightsPanel } from '@features/scanner/WeightsPanel'

import './scanner.css'

const TIMEFRAMES = ['M5', 'M15', 'H1', 'H4'] as const

// The seeded universe. Hard-coded because §18.4's `/scanner/universe` row is
// not implemented, and inventing a symbol list from the feed's rows would make
// the board silently narrower than the scan it claims to summarise.
const SYMBOLS = ['BTCUSDT', 'ETHUSDT'] as const

interface Board {
  readonly rows: readonly RankedRow[]
  readonly gatePassers: number | null
  readonly belowFloor: number | null
  readonly meta: Meta
}

export function RankingsScreen() {
  const [timeframe, setTimeframe] = useState<string>('H1')
  const [board, setBoard] = useState<Board | null>(null)
  const [weights, setWeights] = useState<Weights | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [weightsError, setWeightsError] = useState<string | null>(null)
  const [busy, setBusy] = useState(true)

  // Loaded once and not per timeframe: §9.1's table is doctrine, the same for
  // every board, and refetching it on each switch would imply otherwise.
  useEffect(() => {
    let cancelled = false

    fetchWeights()
      .then((response) => {
        if (!cancelled) setWeights(response.data)
      })
      .catch((cause: unknown) => {
        if (!cancelled) setWeightsError(message(cause))
      })

    return () => {
      cancelled = true
    }
  }, [])

  const load = useCallback(async () => {
    setBusy(true)
    setError(null)

    try {
      const response = await fetchRankings(SYMBOLS, timeframe)

      setBoard({
        rows: response.data,
        gatePassers: response.page?.gate_passers ?? null,
        belowFloor: response.page?.below_floor ?? null,
        meta: response.meta,
      })
    } catch (cause) {
      setError(message(cause))
    } finally {
      setBusy(false)
    }
  }, [timeframe])

  useEffect(() => {
    void load()
  }, [load])

  return (
    <section className="scanner">
      <header className="scanner__header">
        <h1 className="scanner__title">Rankings</h1>

        <label>
          Timeframe
          <select
            value={timeframe}
            onChange={(event) => setTimeframe(event.target.value)}
            aria-label="Timeframe"
          >
            {TIMEFRAMES.map((option) => (
              <option key={option} value={option}>
                {option}
              </option>
            ))}
          </select>
        </label>

        {busy && <span role="status">Loading…</span>}
      </header>

      {error !== null && (
        <p role="alert" data-testid="rankings-error">
          {error}
        </p>
      )}

      {board !== null && error === null && (
        <>
          {/* The denominator, always, and not only when the board is empty.
              §8.6 keeps below-floor candidates "for calibration", and a board
              showing only its rows makes a quiet market and a broken pipeline
              look identical -- the confusion that cost days on the host, where
              64 candidates scored and none published. */}
          <p className="scanner__denominator" data-testid="denominator">
            {board.gatePassers === null
              ? 'The board did not report how many candidates were scored.'
              : `${board.rows.length} published · ${board.gatePassers} scored · ` +
                `${board.belowFloor ?? 0} below the floor`}
          </p>

          {board.rows.length === 0 ? (
            <p className="quiet__reason" data-testid="rankings-empty">
              {board.gatePassers === 0
                ? 'Nothing reached the gates at this close.'
                : 'Everything that scored stopped below its archetype floor.'}
            </p>
          ) : (
            <table className="scanner__board" data-testid="rankings-board">
              <caption className="scanner__caption">
                Ordered by §9.2 — confidence, then archetype, then timeframe, then tier
              </caption>
              <thead>
                <tr>
                  <th scope="col">#</th>
                  <th scope="col">Symbol</th>
                  <th scope="col">Direction</th>
                  <th scope="col">Archetype</th>
                  <th scope="col">Tier</th>
                  <th scope="col">Confidence</th>
                  <th scope="col">Display</th>
                </tr>
              </thead>
              <tbody>
                {board.rows.map((row) => (
                  <tr key={`${row.symbol}-${row.direction}`} data-testid={`ranked-${row.rank}`}>
                    <td>{row.rank}</td>
                    <th scope="row">{row.symbol}</th>
                    <td>{row.direction === 'UP' ? 'Long' : 'Short'}</td>
                    <td>{row.archetype}</td>
                    <td>{row.tier}</td>
                    {/* Both numbers, as on the feed: §9.3 decays one and not
                        the other, and one alone cannot tell a weakening signal
                        from a weak one (§15.4). */}
                    <td className="meter__value">{row.confidence}</td>
                    <td className="meter__value">{row.display_rank}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </>
      )}

      <WeightsPanel weights={weights} error={weightsError} />
    </section>
  )
}

function message(cause: unknown): string {
  return cause instanceof ApiRequestError
    ? `${cause.code}: ${cause.message} (${cause.correlationId})`
    : 'The request failed.'
}
