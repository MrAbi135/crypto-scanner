// The S13a screen: pick a context, see the doctrine drawn on it.
//
// Deliberately one screen with no routing, no state library and no design
// system. Those are S13-S16. This exists to make the engine's output visible
// and, per Roadmap §8.2, to be the instrument the developer verifies golden
// labels with -- so its job is to be honest, not finished.

import { useEffect, useState } from 'react'

import type { Meta, Candle, Pool, StructureEvent, Sweep, Zone } from '@entities/market/types'
import {
  ApiRequestError,
  fetchCandles,
  fetchLiquidity,
  fetchStructure,
  fetchZones,
} from '@services/api/client'
import { Chart } from '@features/chart/Chart'
import { EvidencePanel } from '@features/chart/EvidencePanel'
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

export function ChartScreen() {
  const [symbol, setSymbol] = useState('BTCUSDT')
  const [timeframe, setTimeframe] = useState<string>('H1')
  const [loaded, setLoaded] = useState<Loaded | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [inspection, setInspection] = useState<Inspection | null>(null)

  useEffect(() => {
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
  }, [symbol, timeframe])

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
            {TIMEFRAMES.map((option) => (
              <option key={option} value={option}>
                {option}
              </option>
            ))}
          </select>
        </label>

        {busy && <span role="status">Loading…</span>}
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

          <EvidencePanel inspection={inspection} onClose={() => setInspection(null)} />

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
