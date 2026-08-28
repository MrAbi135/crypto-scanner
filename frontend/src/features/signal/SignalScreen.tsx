// S16's signal detail — the conviction surface (Blueprint §21.3, PRD FC-3.2).
//
// The screen a reader opens to decide whether to believe a signal, which sets
// its one rule: **everything on it is the record, nothing on it is a
// retelling.** Every value renders as the API sent it — canonical decimal
// strings, the sealed payload's own words, the server's own hash verdict. The
// screen adds arrangement, never arithmetic.
//
// Three requests, not one round trip stitched server-side: detail (the seal
// and outcome), evidence (§15.4's breakdown), transitions (§12's history).
// They load independently so a slow history does not blank the factors — but
// a *failed* section renders as failed, never as empty, because on this screen
// "no stress tests" is a claim and must not be manufactured by a 500.

import { useEffect, useState } from 'react'

import type {
  FactorWeight,
  SignalDetail,
  SignalEvidence,
  SignalTransition,
} from '@entities/market/types'
import {
  ApiRequestError,
  fetchSignalDetail,
  fetchSignalEvidence,
  fetchSignalTransitions,
  fetchWeights,
} from '@services/api/client'

import './signal.css'

export interface SignalScreenProps {
  readonly signalId: string
  /** Open this signal's own context on the chart, entry zone selected. */
  readonly onOpenChart?:
    | ((symbol: string, timeframe: string, object?: string) => void)
    | undefined
}

type Slot<T> = { kind: 'loading' } | { kind: 'error'; message: string } | { kind: 'ready'; value: T }

function describe(cause: unknown): string {
  return cause instanceof ApiRequestError
    ? `${cause.code}: ${cause.message} (${cause.correlationId})`
    : 'The request failed.'
}

export function SignalScreen({ signalId, onOpenChart }: SignalScreenProps) {
  const [detail, setDetail] = useState<Slot<SignalDetail>>({ kind: 'loading' })
  const [evidence, setEvidence] = useState<Slot<SignalEvidence>>({ kind: 'loading' })
  const [transitions, setTransitions] = useState<Slot<readonly SignalTransition[]>>({
    kind: 'loading',
  })
  // Factor names and weights come from §9.1's published table, not from a
  // constant here: the server's table is the doctrine's, and a local copy
  // would drift the day a weight is amended. Absent, the factors render under
  // their bare keys rather than blocking the screen.
  const [weights, setWeights] = useState<readonly FactorWeight[] | null>(null)

  useEffect(() => {
    let cancelled = false

    setDetail({ kind: 'loading' })
    setEvidence({ kind: 'loading' })
    setTransitions({ kind: 'loading' })

    fetchSignalDetail(signalId)
      .then((r) => !cancelled && setDetail({ kind: 'ready', value: r.data }))
      .catch((c) => !cancelled && setDetail({ kind: 'error', message: describe(c) }))

    fetchSignalEvidence(signalId)
      .then((r) => !cancelled && setEvidence({ kind: 'ready', value: r.data }))
      .catch((c) => !cancelled && setEvidence({ kind: 'error', message: describe(c) }))

    fetchSignalTransitions(signalId)
      .then((r) => !cancelled && setTransitions({ kind: 'ready', value: r.data }))
      .catch((c) => !cancelled && setTransitions({ kind: 'error', message: describe(c) }))

    fetchWeights()
      .then((r) => !cancelled && setWeights(r.data.factors))
      .catch(() => undefined)

    return () => {
      cancelled = true
    }
  }, [signalId])

  if (detail.kind === 'loading') {
    return (
      <section className="signal-screen">
        <p role="status">Loading signal…</p>
      </section>
    )
  }

  if (detail.kind === 'error') {
    return (
      <section className="signal-screen">
        <p role="alert" data-testid="signal-error">
          {detail.message}
        </p>
      </section>
    )
  }

  const row = detail.value
  const long = row.direction === 'UP'

  return (
    <section className="signal-screen" data-testid="signal-screen">
      <header className="signal-screen__header">
        <h1 className="signal-screen__title">
          {row.symbol} {row.timeframe}{' '}
          <span className={`signal__direction--${long ? 'up' : 'down'}`}>
            {long ? 'Long' : 'Short'}
          </span>{' '}
          — {row.archetype}, grade {row.grade}
        </h1>

        <p className="signal-screen__meta">
          published {row.published_at} · lifecycle{' '}
          <span data-testid="signal-lifecycle-state">{row.lifecycle_state ?? 'unrecorded'}</span> ·
          TTL {row.ttl_candles} candles
        </p>

        {onOpenChart !== undefined && (
          <button
            type="button"
            className="signal-screen__chart"
            data-testid="signal-open-chart"
            onClick={() =>
              onOpenChart(
                row.symbol,
                row.timeframe,
                // The entry zone, pre-selected: the whole reason the link
                // exists is landing on the object the claim is priced from.
                evidence.kind === 'ready' && evidence.value.entry_zone_id !== null
                  ? `zone:${evidence.value.entry_zone_id}`
                  : undefined,
              )
            }
          >
            Open {row.symbol} {row.timeframe} on the chart
          </button>
        )}
      </header>

      {/* §12.4's verdict, before the argument. A reader must not study the
          factors of a signal that already failed without knowing it failed --
          §15.4: "the platform's record is its integrity". */}
      {row.outcome !== undefined && (
        <p
          className={`signal-screen__outcome signal-screen__outcome--${row.outcome.outcome.toLowerCase()}`}
          data-testid="signal-outcome"
        >
          {row.outcome.outcome} after {row.outcome.elapsed_candles} candles — MFE{' '}
          {row.outcome.mfe_r}R, MAE {row.outcome.mae_r}R (resolved {row.outcome.resolved_at})
        </p>
      )}

      <Levels row={row} />

      <Confidence evidence={evidence} weights={weights} confidence={row.confidence} />

      <Lifecycle transitions={transitions} />

      {/* Provenance: which code made the call, and whether the record still
          hashes to what was sealed. `payload_hash_verified` is recomputed by
          the server on this read, not trusted from a column. */}
      <footer className="signal-screen__provenance" data-testid="signal-provenance">
        <span>algo {row.versions.algo_version}</span>
        <span>params {row.versions.param_set_version}</span>
        {row.payload_hash !== undefined && (
          <span data-testid="signal-hash">
            seal {row.payload_hash.slice(0, 12)}…{' '}
            {row.payload_hash_verified === true ? 'verified against the payload' : (
              <strong>DOES NOT MATCH THE PAYLOAD</strong>
            )}
          </span>
        )}
      </footer>
    </section>
  )
}

/** C14: entry, invalidation and targets as the three priced rows of §15.2. */
function Levels({ row }: { readonly row: SignalDetail }) {
  const risk = payloadRisk(row)

  return (
    <dl className="signal-screen__levels" data-testid="signal-levels">
      <div>
        <dt>entry</dt>
        <dd>
          {row.entry.proximal} – {row.entry.distal}
        </dd>
      </div>
      <div>
        <dt>invalidation</dt>
        <dd>{row.invalidation}</dd>
      </div>
      <div>
        <dt>target</dt>
        <dd>
          {row.targets.primary === null
            ? '—'
            : `${row.targets.primary.low}${
                row.targets.primary.low === row.targets.primary.high
                  ? ''
                  : ` – ${row.targets.primary.high}`
              } (pool strength ${row.targets.primary.strength})`}
        </dd>
      </div>
      {row.targets.secondary !== null && (
        <div>
          <dt>secondary</dt>
          <dd>
            {row.targets.secondary.low} – {row.targets.secondary.high}
          </dd>
        </div>
      )}
      {risk !== null && (
        <div>
          <dt>R multiple</dt>
          {/* The seal's own figure. Recomputing it here from the levels would
              put §12.4's arithmetic in two places and let them disagree. */}
          <dd data-testid="signal-r-multiple">{risk}</dd>
        </div>
      )}
    </dl>
  )
}

function payloadRisk(row: SignalDetail): string | null {
  const risk = row.payload?.['risk']

  if (typeof risk !== 'object' || risk === null) return null

  const value = (risk as Record<string, unknown>)['r_multiple']

  return typeof value === 'string' ? value : null
}

/**
 * SLS §15.4: confidence "is displayed with its factor breakdown — never as a
 * bare number". This is the component that discharges that sentence, which
 * the feed's meter could not: the feed row does not carry the factors, and
 * until this screen existed the number was shown everywhere it appeared
 * without the breakdown anywhere.
 */
function Confidence({
  evidence,
  weights,
  confidence,
}: {
  readonly evidence: Slot<SignalEvidence>
  readonly weights: readonly FactorWeight[] | null
  readonly confidence: string
}) {
  if (evidence.kind === 'loading') {
    return <p role="status">Loading the factor breakdown…</p>
  }

  if (evidence.kind === 'error') {
    // The number is deliberately not shown alone while the breakdown is
    // unavailable -- rendering it bare here is exactly what §15.4 forbids,
    // and an error is not an exemption.
    return (
      <p role="alert" data-testid="signal-evidence-error">
        The factor breakdown could not be read, so the confidence is not shown bare:{' '}
        {evidence.message}
      </p>
    )
  }

  const byKey = new Map((weights ?? []).map((w) => [w.factor, w]))
  const factors = Object.entries(evidence.value.confidence.factors)

  return (
    <section data-testid="signal-confidence">
      <h2 className="signal-screen__subtitle">
        Confidence {confidence} — grade {evidence.value.confidence.grade}
      </h2>

      {evidence.value.reason !== null && (
        <p className="signal-screen__reason" data-testid="signal-reason">
          {evidence.value.reason}
        </p>
      )}

      <table className="signal-screen__factors" data-testid="signal-factors">
        <caption className="signal-screen__caption">
          §8.3's factors as sealed at publication; weights from §9.1's published table
        </caption>
        <thead>
          <tr>
            <th scope="col">Factor</th>
            <th scope="col">Score</th>
            <th scope="col">Weight</th>
          </tr>
        </thead>
        <tbody>
          {factors.map(([key, score]) => {
            const named = byKey.get(key)

            return (
              <tr key={key} data-testid={`factor-${key}`}>
                {/* The server's name for the factor, or the bare key while the
                    weights table is unavailable -- never a name invented here. */}
                <th scope="row">{named === undefined ? key : `${key} — ${named.name}`}</th>
                <td className="signal-screen__mono">{score}</td>
                <td className="signal-screen__mono">
                  {named === undefined ? '—' : `${named.weight_pct}%`}
                </td>
              </tr>
            )
          })}
        </tbody>
      </table>

      <p className="signal-screen__chain" data-testid="signal-evidence-ids">
        Evidence chain: {evidence.value.evidence_ids.length} object
        {evidence.value.evidence_ids.length === 1 ? '' : 's'}
        {evidence.value.evidence_ids.length > 0 &&
          ` (${evidence.value.evidence_ids.map((id) => id.slice(0, 12)).join(', ')}…)`}
      </p>

      {Object.keys(evidence.value.htf_chain).length > 0 && (
        <p className="signal-screen__chain" data-testid="signal-htf">
          HTF chain at publication:{' '}
          {Object.entries(evidence.value.htf_chain)
            .map(([tf, state]) => `${tf}: ${state}`)
            .join(' · ')}
        </p>
      )}
    </section>
  )
}

/** §12's state history, stress tests marked (§18.8: "incl. stress_test"). */
function Lifecycle({
  transitions,
}: {
  readonly transitions: Slot<readonly SignalTransition[]>
}) {
  if (transitions.kind === 'loading') {
    return <p role="status">Loading the lifecycle…</p>
  }

  if (transitions.kind === 'error') {
    // Failed is not empty. An empty history claims "nothing has happened to
    // this signal", which a 500 must not be allowed to assert.
    return (
      <p role="alert" data-testid="signal-transitions-error">
        The lifecycle history could not be read: {transitions.message}
      </p>
    )
  }

  return (
    <section data-testid="signal-lifecycle">
      <h2 className="signal-screen__subtitle">Lifecycle</h2>

      {transitions.value.length === 0 ? (
        <p data-testid="signal-lifecycle-empty">No transitions recorded.</p>
      ) : (
        <ol className="signal-screen__timeline">
          {transitions.value.map((t, index) => (
            <li key={`${t.to_state}-${t.recorded_at}-${index}`}>
              <span className="signal-screen__mono">{t.at_candle_open_time}</span>{' '}
              {t.from_state === null ? '' : `${t.from_state} → `}
              {t.to_state}
              {/* §12.4: a wick through invalidation records stress_test and
                  does not fail the signal. Shown in words -- it is the single
                  most misread event in the lifecycle. */}
              {t.stress_test && (
                <em data-testid="signal-stress-test">
                  {' '}
                  — stress test: wick through invalidation, close held (§12.4)
                </em>
              )}
              {t.refresh && ' — refresh'}
            </li>
          ))}
        </ol>
      )}
    </section>
  )
}
