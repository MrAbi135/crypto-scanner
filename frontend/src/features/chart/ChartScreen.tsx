// The S13a screen: pick a context, see the doctrine drawn on it.
//
// Deliberately one screen with no routing, no state library and no design
// system. Those are S13-S16. This exists to make the engine's output visible
// and, per Roadmap §8.2, to be the instrument the developer verifies golden
// labels with -- so its job is to be honest, not finished.

import { useEffect, useRef, useState } from 'react'

import type { Meta, Candle, Pool, StructureEvent, Sweep, Zone } from '@entities/market/types'
import {
  ApiRequestError,
  fetchCandles,
  fetchLiquidity,
  fetchStructure,
  fetchZones,
  logout,
} from '@services/api/client'
import { currentSession } from '@services/api/session'
import { Chart } from '@features/chart/Chart'
import { inspectPool, inspectZone } from '@features/chart/inspection'
import { EventTimeline } from '@features/chart/EventTimeline'
import { EvidencePanel } from '@features/chart/EvidencePanel'
import { SignIn } from '@features/chart/SignIn'
import type { Inspection } from '@features/chart/inspection'

import './chart.css'

const TIMEFRAMES = ['M5', 'M15', 'H1', 'H4'] as const

interface Loaded {
  readonly candles: readonly Candle[]
  readonly zones: readonly Zone[]
  readonly pools: readonly Pool[]
  readonly structure: readonly StructureEvent[]
  readonly sweeps: readonly Sweep[]
  readonly meta: Meta
  readonly doctrineMeta: Meta
}

export interface ChartScreenProps {
  /**
   * The context to open on, when something sent the reader here.
   *
   * Applied on mount, and again **only when this value itself changes** --
   * never on every render. The difference is the whole design:
   *
   *   * a caller that mirrors this screen's own reports back into `openOn`
   *     (App does, through the URL) gets a working back button, because a
   *     history entry arriving from outside is a change and re-seeds;
   *   * a caller that passes a fixed context does not get a leash: the reader
   *     types another symbol, `openOn` has not changed, and nothing pushes
   *     them home.
   *
   * An effect keyed on the current *state* instead would collapse both cases
   * into the leash.
   */
  readonly openOn?:
    | { readonly symbol: string; readonly timeframe: string; readonly object?: string }
    | undefined
  /**
   * Report the context on screen, so an address bar (or anything else) can
   * follow it. Includes the inspected object, because that selection is what
   * makes an evidence link worth sending to someone.
   */
  readonly onContext?:
    | ((next: { symbol: string; timeframe: string; object: string | null }) => void)
    | undefined
}

export function ChartScreen({ openOn, onContext }: ChartScreenProps = {}) {
  const [symbol, setSymbol] = useState(openOn?.symbol ?? 'BTCUSDT')
  const [timeframe, setTimeframe] = useState<string>(openOn?.timeframe ?? 'H1')
  const [loaded, setLoaded] = useState<Loaded | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [inspection, setInspection] = useState<Inspection | null>(null)
  const [signedIn, setSignedIn] = useState(currentSession() !== null)
  const [wanted, setWanted] = useState<string | null>(openOn?.object ?? null)
  const [unresolved, setUnresolved] = useState<string | null>(null)
  const lastOpenOn = useRef(signature(openOn))

  // Re-seed when the caller's context changes, not when ours does. See the
  // prop's own comment: this is what separates a working back button from a
  // leash, and the two are one `useRef` apart.
  useEffect(() => {
    const next = signature(openOn)

    if (next === lastOpenOn.current) return

    lastOpenOn.current = next

    if (openOn === undefined) return

    setSymbol(openOn.symbol)
    setTimeframe(openOn.timeframe)
    setWanted(openOn.object ?? null)
    setInspection(null)
    setUnresolved(null)
  }, [openOn])

  useEffect(() => {
    onContext?.({ symbol, timeframe, object: inspection?.id ?? wanted })
  }, [onContext, symbol, timeframe, inspection, wanted])

  // S15's "evidence deep-link → highlight": an address carrying an object id
  // opens with that object's evidence, once its data has arrived.
  //
  // **Zones and pools only, and the panel says so when it is neither.** Their
  // ids are the API's own (`zone_id`, `pool_id`) and survive a page load. A
  // sweep's and a swing's are built from chart-local markers, so a link to one
  // would resolve to a different object -- or to nothing -- on a window that
  // has moved by a candle. That is the [[window-local-index-trap]] wearing a
  // URL, and the fix is upstream: stable ids for those two, not a lookup here
  // that is right most of the time.
  useEffect(() => {
    if (wanted === null || loaded === null) return

    const [kind, id] = [wanted.slice(0, wanted.indexOf(':')), wanted.slice(wanted.indexOf(':') + 1)]

    const zone = kind === 'zone' ? loaded.zones.find((row) => row.zone_id === id) : undefined
    const pool = kind === 'pool' ? loaded.pools.find((row) => row.pool_id === id) : undefined

    if (zone !== undefined) setInspection(inspectZone(zone))
    else if (pool !== undefined) setInspection(inspectPool(pool))
    else setUnresolved(wanted)

    setWanted(null)
  }, [wanted, loaded])

  useEffect(() => {
    if (!signedIn) return

    let cancelled = false

    async function load() {
      setBusy(true)
      setError(null)
      // The selection belongs to the context it was made in. Left standing
      // across a symbol or timeframe change, the panel would show one
      // context's evidence beside another's chart -- and this screen exists to
      // be trusted about exactly that pairing.
      setInspection(null)

      try {
        const [candles, zones, liquidity, structure] = await Promise.all([
          fetchCandles(symbol, timeframe),
          fetchZones(symbol, timeframe),
          fetchLiquidity(symbol, timeframe),
          fetchStructure(symbol, timeframe),
        ])

        if (cancelled) return

        setLoaded({
          candles: candles.data,
          zones: zones.data,
          pools: liquidity.data.pools,
          sweeps: liquidity.data.sweeps,
          structure: structure.data,
          meta: candles.meta,
          doctrineMeta: zones.meta,
        })
      } catch (cause) {
        if (cancelled) return

        // The correlation id is shown, not hidden. It is the only thing that
        // makes a user-reported problem findable in the logs.
        setError(
          cause instanceof ApiRequestError
            ? `${cause.code}: ${cause.message} (${cause.correlationId})`
            : 'The request failed.',
        )
      } finally {
        if (!cancelled) setBusy(false)
      }
    }

    void load()

    return () => {
      cancelled = true
    }
  }, [symbol, timeframe, signedIn])

  if (!signedIn) {
    return (
      <section className="screen">
        <SignIn onSignedIn={() => setSignedIn(true)} />
      </section>
    )
  }

  return (
    <section className="screen">
      <header className="screen__controls">
        <label>
          Symbol
          <input
            value={symbol}
            onChange={(event) => setSymbol(event.target.value.toUpperCase())}
            aria-label="Symbol"
          />
        </label>

        <label>
          Timeframe
          <select
            value={timeframe}
            onChange={(event) => setTimeframe(event.target.value)}
            aria-label="Timeframe"
          >
            {/* Whatever we were opened on is offered even if it is not one of
                the four: a `select` whose value is not among its options
                renders blank, and a blank timeframe beside a loaded chart is a
                lie about which context is on screen. */}
            {timeframes(timeframe).map((option) => (
              <option key={option} value={option}>
                {option}
              </option>
            ))}
          </select>
        </label>

        {busy && <span role="status">Loading…</span>}

        <button
          type="button"
          className="screen__signout"
          onClick={() => {
            void logout().finally(() => setSignedIn(false))
          }}
        >
          Sign out
        </button>
      </header>

      {error && (
        <p role="alert" data-testid="chart-error">
          {error}
        </p>
      )}

      {loaded && (
        <>
          <Chart
            candles={loaded.candles}
            zones={loaded.zones}
            pools={loaded.pools}
            structure={loaded.structure}
            sweeps={loaded.sweeps}
            onInspect={setInspection}
            selectedId={inspection?.id ?? null}
          />

          {/* Not silent. A link that lands on a chart with nothing selected is
              indistinguishable from a link that worked and pointed at an object
              the reader then failed to notice. */}
          {unresolved !== null && inspection === null && (
            <p role="status" data-testid="chart-unresolved">
              The linked object <code>{unresolved}</code> is not on this chart. Zone and pool
              links survive; sweep and swing ids are built per render and do not.
            </p>
          )}

          <EvidencePanel inspection={inspection} onClose={() => setInspection(null)} />

          {/* Everything the structure endpoint returned that the chart does
              not draw. Fetched all along and discarded until now. */}
          <EventTimeline events={loaded.structure} />

          {/* Provenance on screen, not buried in a network tab. A chart whose
              algo_version the viewer cannot see is a chart they cannot use to
              verify anything -- and verification is what this screen is for. */}
          <footer className="screen__provenance" data-testid="provenance">
            <span>freshness: {loaded.meta.freshness.state}</span>
            <span>candles: {loaded.candles.length}</span>
            <span>zones: {loaded.zones.length}</span>
            <span>pools: {loaded.pools.length}</span>
            {loaded.doctrineMeta.versions && (
              <span>
                algo: {loaded.doctrineMeta.versions.algo_version} · params:{' '}
                {loaded.doctrineMeta.versions.param_set_version}
              </span>
            )}
          </footer>
        </>
      )}
    </section>
  )
}

function timeframes(selected: string): readonly string[] {
  return TIMEFRAMES.includes(selected as (typeof TIMEFRAMES)[number])
    ? TIMEFRAMES
    : [...TIMEFRAMES, selected]
}

function signature(openOn: ChartScreenProps['openOn']): string {
  return openOn === undefined
    ? ''
    : `${openOn.symbol}|${openOn.timeframe}|${openOn.object ?? ''}`
}
