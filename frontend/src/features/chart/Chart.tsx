// The chart. SVG rather than a charting library: overlay placement is the
// thing being verified here, and a library that owns the coordinate space
// would put the part that must be trustworthy behind an abstraction.

import type { Candle, Pool, Zone } from '@entities/market/types'
import {
  bodyRect,
  isUp,
  priceScale,
  timeScale,
  xForTime,
  type Viewport,
} from '@features/chart/scale'

const VIEWPORT: Viewport = { width: 1200, height: 600, padding: 24 }

export interface ChartProps {
  readonly candles: readonly Candle[]
  readonly zones: readonly Zone[]
  readonly pools: readonly Pool[]
}

export function Chart({ candles, zones, pools }: ChartProps) {
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

  const price = priceScale(candles, VIEWPORT, zones)
  const time = timeScale(candles.length, VIEWPORT)

  return (
    <svg
      viewBox={`0 0 ${VIEWPORT.width} ${VIEWPORT.height}`}
      className="chart"
      role="img"
      aria-label={`Price chart with ${candles.length} candles, ${zones.length} zones and ${pools.length} liquidity pools`}
      data-testid="chart"
    >
      {/* Zones first: they are context and belong behind the price. */}
      <g data-testid="zones">
        {zones.map((zone) => {
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
    </svg>
  )
}
