// The chart. SVG rather than a charting library: overlay placement is the
// thing being verified here, and a library that owns the coordinate space
// would put the part that must be trustworthy behind an abstraction.

import type { Candle, Pool, StructureEvent, Zone } from '@entities/market/types'
import {
  bodyRect,
  isUp,
  priceScale,
  timeScale,
  visibleZones,
  xForTime,
  type Viewport,
} from '@features/chart/scale'
import { swingMarkers } from '@features/chart/swings'

const VIEWPORT: Viewport = { width: 1200, height: 600, padding: 24 }

export interface ChartProps {
  readonly candles: readonly Candle[]
  readonly zones: readonly Zone[]
  readonly pools: readonly Pool[]
  readonly structure: readonly StructureEvent[]
}

export function Chart({ candles, zones, pools, structure }: ChartProps) {
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
  const swings = swingMarkers(structure)

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
        role="img"
        aria-label={`Price chart with ${candles.length} candles, ${shown.length} of ${zones.length} zones in view, ${pools.length} liquidity pools and ${swings.length} swings`}
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
                data-testid={`zone-${zone.zone_id}`}
                data-zone-type={zone.zone_type}
                data-state={zone.state}
                x={left}
                y={top}
                width={Math.max(1, VIEWPORT.width - VIEWPORT.padding - left)}
                height={Math.max(1, bottom - top)}
                className={`zone zone--${zone.polarity.toLowerCase()}${
                  zone.stale_context ? ' zone--stale' : ''
                }`}
              />
            )
          })}
        </g>

        <g data-testid="pools">
          {pools.map((pool) => (
            <line
              key={pool.pool_id}
              data-testid={`pool-${pool.pool_id}`}
              data-side={pool.side}
              x1={VIEWPORT.padding}
              x2={VIEWPORT.width - VIEWPORT.padding}
              y1={price.y(pool.price)}
              y2={price.y(pool.price)}
              className={`pool pool--${pool.side.toLowerCase()}`}
            />
          ))}
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

            return (
              <circle
                key={swing.key}
                data-testid={`swing-${swing.key}`}
                data-kind={swing.kind}
                data-strength={swing.strength}
                data-price={swing.price}
                cx={cx}
                cy={cy}
                r={radius}
                className={`swing swing--${swing.kind.toLowerCase()} swing--${swing.strength.toLowerCase()}`}
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
