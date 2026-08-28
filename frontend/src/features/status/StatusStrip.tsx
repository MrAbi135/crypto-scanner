// §18.3's status strip (PRD FC-1.2's data honesty surface).
//
// **The failure mode this component exists to avoid is looking fine.** A strip
// that renders "0 behind" because its own fetch failed, or that shows two of
// the four things §18.3 asks for without saying which two are missing, is worse
// than no strip: it converts "we did not look" into "we looked and it is well".
// So an unloaded strip says unknown, an errored strip says errored, and what
// the server declares it cannot measure is printed on the face of it.

import { useCallback, useEffect, useState } from 'react'

import type { PlatformStatus } from '@entities/market/types'
import { ApiRequestError, fetchStatus } from '@services/api/client'

import './status.css'

/** Re-asked on a timer: a status strip that is only correct at page load is a
 *  screenshot. Slow, because nothing here changes faster than a close. */
const REFRESH_MS = 60_000

type State =
  | { readonly kind: 'loading' }
  | { readonly kind: 'error'; readonly message: string }
  | { readonly kind: 'ready'; readonly status: PlatformStatus }

function readable(body: PlatformStatus | undefined): body is PlatformStatus {
  return (
    body !== undefined &&
    body !== null &&
    Array.isArray(body.feeds) &&
    Array.isArray(body.degraded) &&
    Array.isArray(body.not_measured) &&
    typeof body.behind_count === 'number' &&
    typeof body.degraded_count === 'number'
  )
}

export function StatusStrip() {
  const [state, setState] = useState<State>({ kind: 'loading' })
  const [open, setOpen] = useState(false)

  const load = useCallback(async () => {
    try {
      const response = await fetchStatus()

      // A body that is not the shape we asked for is an unread status, not an
      // empty one. Coercing the missing fields to zero would render a green
      // "all 0 feeds covered" strip out of a response nobody understood --
      // exactly the reassurance-from-absence this component exists to refuse.
      if (!readable(response.data)) {
        setState({ kind: 'error', message: 'The status response was not the expected shape.' })

        return
      }

      setState({ kind: 'ready', status: response.data })
    } catch (cause) {
      setState({
        kind: 'error',
        message:
          cause instanceof ApiRequestError
            ? `${cause.code}: ${cause.message} (${cause.correlationId})`
            : 'The platform status could not be read.',
      })
    }
  }, [])

  useEffect(() => {
    void load()

    const timer = setInterval(() => void load(), REFRESH_MS)

    return () => clearInterval(timer)
  }, [load])

  if (state.kind === 'loading') {
    return (
      <p className="status status--unknown" data-testid="status-strip" role="status">
        Platform status: reading…
      </p>
    )
  }

  if (state.kind === 'error') {
    // Not a silent hide and not a green strip. An unread status is its own
    // state, and it is the one most likely to matter.
    return (
      <p className="status status--unknown" data-testid="status-strip" role="alert">
        Platform status unknown — {state.message}
      </p>
    )
  }

  const { status } = state
  const troubled = status.behind_count > 0 || status.degraded_count > 0

  return (
    <section
      className={`status${troubled ? ' status--troubled' : ' status--ok'}`}
      data-testid="status-strip"
      aria-label="Platform status"
    >
      <p className="status__line">
        <span data-testid="status-behind">
          {status.behind_count === 0
            ? `All ${status.feeds.length} feeds covered`
            : `${status.behind_count} of ${status.feeds.length} feeds behind`}
        </span>
        {' · '}
        <span data-testid="status-degraded">
          {status.degraded_count === 0
            ? 'no open incidents'
            : `${status.degraded_count} open incident${status.degraded_count === 1 ? '' : 's'}`}
        </span>
        {troubled && (
          <>
            {' '}
            <button
              type="button"
              className="status__more"
              aria-expanded={open}
              data-testid="status-toggle"
              onClick={() => setOpen(!open)}
            >
              {open ? 'Hide detail' : 'Show detail'}
            </button>
          </>
        )}
      </p>

      {open && (
        <div data-testid="status-detail">
          {status.behind_count > 0 && (
            <ul className="status__list">
              {status.feeds
                .filter((feed) => feed.coverage === 'BEHIND')
                .map((feed) => (
                  <li key={`${feed.symbol}-${feed.timeframe}`}>
                    {feed.symbol} {feed.timeframe} — behind by {feed.candles_behind}
                  </li>
                ))}
            </ul>
          )}

          {status.degraded_count > 0 && (
            <ul className="status__list">
              {status.degraded.map((row) => (
                <li key={row.id}>
                  {row.type} — {row.symbol ?? 'platform'} {row.timeframe ?? ''} since{' '}
                  {row.started_at}
                </li>
              ))}
            </ul>
          )}
        </div>
      )}

      {/* The server's own list of what it did not measure. Printed rather than
          dropped, and not derived here: the API knows what it could not see,
          and a hard-coded copy of that list would go stale the day one of them
          becomes measurable. */}
      {status.not_measured.length > 0 && (
        <p className="status__gap" data-testid="status-not-measured">
          Not measured here: {status.not_measured.join('; ')}.
        </p>
      )}
    </section>
  )
}
