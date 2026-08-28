// The chart. SVG rather than a charting library: overlay placement is the
// thing being verified here, and a library that owns the coordinate space
// would put the part that must be trustworthy behind an abstraction.

import type { KeyboardEvent } from 'react'

import type { Candle, Pool, StructureEvent, Sweep, Zone } from '@entities/market/types'
import {
  bodyRect,
  isUp,
  priceScale,
  timeScale,
  visibleZones,
  xForTime,
  type Viewport,
} from '@features/chart/scale'
import {
  inspectPool,
  inspectSweep,
  inspectSwing,
  inspectZone,
  type Inspection,
} from '@features/chart/inspection'
import { sweepKey, sweepMarkers } from '@features/chart/sweeps'
import { swingKey, swingMarkers } from '@features/chart/swings'
import { isTreated } from '@features/chart/zoneState'

const VIEWPORT: Viewport = { width: 1200, height: 600, padding: 24 }

export interface ChartProps {
  readonly candles: readonly Candle[]
  readonly zones: readonly Zone[]
  readonly pools: readonly Pool[]
  readonly structure: readonly StructureEvent[]
  readonly sweeps: readonly Sweep[]
  /** Optional: a chart with no inspector is still a chart. */
  readonly onInspect?: (inspection: Inspection) => void
  readonly selectedId?: string | null
}

export function Chart({
  candles,
  zones,
  pools,
  structure,
  sweeps,
  onInspect,
  selectedId = null,
}: ChartProps) {
  if (candles.length === 0) {
    // Not an error state and not a blank frame. "No candles" and "the engine
    // found nothing" look identical on an empty chart, and they mean opposite
    // things, so this says which one it is.
    return (
      <p role="status" data-testid="chart-empty">
        No candles for this context yet.
      </p>
    )
  }

  const price = priceScale(candles, VIEWPORT)
  const time = timeScale(candles.length, VIEWPORT)

  // Objects far from price are clipped so the candles stay readable, and
  // the count is surfaced so nothing vanishes without the chart saying so.
  const { shown, clipped } = visibleZones(zones, price)

  // A state this build has no treatment for is drawn loudly and counted, not
  // quietly rendered as though it were FRESH. See `zoneState.ts`: the silent
  // default is exactly the failure §16.3's scale was added to end, and it comes
  // back on its own the next time the server grows a state.
  const untreated = shown.filter((zone) => !isTreated(zone.state))
  const swings = swingMarkers(structure)
  const taken = sweepMarkers(sweeps)

  // Source objects by marker key. The markers do not carry them on purpose --
  // see `swingKey` -- and inspection needs the whole recorded row.
  const swingSource = new Map(structure.map((event) => [swingKey(event), event]))
  const sweepSource = new Map(sweeps.map((sweep) => [sweepKey(sweep), sweep]))

  /**
   * Make an overlay answer for itself.
   *
   * It deliberately returns no `className`. Each overlay writes its own after
   * this spread, so one here would be overwritten -- selection would reach a
   * screen reader through `aria-pressed` and be invisible to everyone else.
   * `selected()` below is what the overlays compose in.
   *
   * `role` and `tabIndex` rather than a click handler alone: these are SVG
   * shapes, which are not focusable and not announced, so a mouse would be the
   * only way to reach the evidence. S13a's DoD asks for an axe-clean screen,
   * and an inspector only a mouse can open is not one.
   */
  const selectable = (inspection: Inspection) =>
    onInspect === undefined
      ? {}
      : {
          role: 'button' as const,
          tabIndex: 0,
          'aria-label': inspection.title,
          'aria-pressed': selectedId === inspection.id,
          onClick: () => onInspect(inspection),
          onKeyDown: (event: KeyboardEvent) => {
            if (event.key !== 'Enter' && event.key !== ' ') return

            // Space scrolls the page otherwise, which moves the chart out from
            // under the thing the reader just selected.
            event.preventDefault()
            onInspect(inspection)
          },
        }

  const selected = (id: string) => (selectedId === id ? ' is-selected' : '')

  return (
    <>
      {clipped > 0 && (
        <p className="chart__clipped" role="status" data-testid="chart-clipped">
          {clipped} of {zones.length} zones sit outside the visible price range and are not drawn.
        </p>
      )}
      <svg
        viewBox={`0 0 ${VIEWPORT.width} ${VIEWPORT.height}`}
        className="chart"
        // `group`, not `img`. An `img` is a single indivisible picture, and
        // the overlays inside this one are focusable buttons -- axe calls that
        // `nested-interactive`, and it is right: a reader told "image" has no
        // way to understand why there are controls inside it. A labelled group
        // is what this actually is, in both modes; without an inspector it is
        // a group whose children happen to be inert.
        role="group"
        aria-label={`Price chart with ${candles.length} candles, ${shown.length} of ${zones.length} zones in view, ${pools.length} liquidity pools, ${swings.length} swings and ${taken.length} sweeps${
          untreated.length === 0
            ? ''
            : `; ${untreated.length} zones are in a state this chart has no treatment for`
        }`}
        data-testid="chart"
      >
        {/* Zones first: they are context and belong behind the price. */}
        <g data-testid="zones">
          {shown.map((zone) => {
            const top = price.y(zone.band_high)
            const bottom = price.y(zone.band_low)

            // A zone runs from when it was created to now. Drawing it full-width
            // would claim it existed before the engine found it -- a false
            // statement to anyone reading the chart to verify a label.
            const left = xForTime(candles, zone.created_at, VIEWPORT, time)

            return (
              <rect
                key={zone.zone_id}
                {...selectable(inspectZone(zone))}
                data-testid={`zone-${zone.zone_id}`}
                data-zone-type={zone.zone_type}
                data-state={zone.state}
                x={left}
                y={top}
                width={Math.max(1, VIEWPORT.width - VIEWPORT.padding - left)}
                height={Math.max(1, bottom - top)}
                className={`zone zone--${zone.polarity.toLowerCase()}${
                  zone.stale_context ? ' zone--stale' : ''
                }${isTreated(zone.state) ? '' : ' zone--untreated'}${selected(
                  `zone:${zone.zone_id}`,
                )}`}
              />
            )
          })}
        </g>

        <g data-testid="pools">
          {pools.map((pool) => (
            <line
              key={pool.pool_id}
              {...selectable(inspectPool(pool))}
              data-testid={`pool-${pool.pool_id}`}
              data-side={pool.side}
              x1={VIEWPORT.padding}
              x2={VIEWPORT.width - VIEWPORT.padding}
              y1={price.y(pool.price)}
              y2={price.y(pool.price)}
              className={`pool pool--${pool.side.toLowerCase()}${selected(`pool:${pool.pool_id}`)}`}
            />
          ))}
        </g>

        {/* After the pools they consume and before the candles: a sweep is
            what happened *to* a level, so it reads against the line it took,
            and it must not cover the wick that made it. */}
        <g data-testid="sweeps">
          {taken.map((sweep) => {
            const x = xForTime(candles, sweep.at, VIEWPORT, time)
            const source = sweepSource.get(sweep.key)

            return (
              <g
                key={sweep.key}
                {...(source === undefined ? {} : selectable(inspectSweep(source, sweep)))}
                data-testid={`sweep-${sweep.key}`}
                data-side={sweep.side}
                data-reclaimed={String(sweep.reclaimed)}
                className={`sweep sweep--${sweep.side.toLowerCase()}${
                  sweep.reclaimed ? ' sweep--reclaimed' : ''
                }${selected(`sweep:${sweep.key}`)}`}
              >
                {/* Level to penetration. §4.6's sweep *is* the reach past the
                    level, so a dot on the level would be a touch. */}
                <line
                  x1={x}
                  x2={x}
                  y1={price.y(sweep.level)}
                  y2={price.y(sweep.penetration)}
                  className="sweep__reach"
                />
                <circle cx={x} cy={price.y(sweep.penetration)} r={3} className="sweep__tip" />
                <title>
                  {`${sweep.side} sweep to ${sweep.penetration} from ${sweep.level}` +
                    `${sweep.depthAtr === null ? '' : ` (${sweep.depthAtr} ATR)`}` +
                    `${sweep.reclaimed ? ' — reclaimed, contrary evidence' : ''}`}
                </title>
              </g>
            )
          })}
        </g>

        <g data-testid="candles">
          {candles.map((candle, index) => {
            const body = bodyRect(candle, index, price, time)
            const centre = time.x(index)
            const up = isUp(candle)

            return (
              <g
                key={candle.open_time}
                data-testid={`candle-${index}`}
                className={up ? 'candle candle--up' : 'candle candle--down'}
              >
                <line
                  x1={centre}
                  x2={centre}
                  y1={price.y(candle.high)}
                  y2={price.y(candle.low)}
                  className="candle__wick"
                />
                <rect x={body.x} y={body.y} width={body.width} height={body.height} />
              </g>
            )
          })}
        </g>

        {/* Last, so they sit above the candles: a swing marks a specific
            high or low, and a marker hidden behind the wick it names is
            worse than no marker. */}
        <g data-testid="swings">
          {swings.map((swing) => {
            const cx = xForTime(candles, swing.at, VIEWPORT, time)
            const cy = price.y(swing.price)

            // External swings are drawn larger. §3.1 nests the two series --
            // every external swing is also an internal one -- so size says
            // which, and neither is hidden behind the other.
            const radius = swing.strength === 'EXTERNAL' ? 5 : 3

            const source = swingSource.get(swing.key)

            return (
              <circle
                key={swing.key}
                {...(source === undefined ? {} : selectable(inspectSwing(source, swing)))}
                data-testid={`swing-${swing.key}`}
                data-kind={swing.kind}
                data-strength={swing.strength}
                data-price={swing.price}
                cx={cx}
                cy={cy}
                r={radius}
                className={`swing swing--${swing.kind.toLowerCase()} swing--${swing.strength.toLowerCase()}${selected(`swing:${swing.key}`)}`}
              >
                <title>
                  {`${swing.strength} ${swing.kind} at ${swing.price} (${swing.at})`}
                </title>
              </circle>
            )
          })}
        </g>
      </svg>
    </>
  )
}
