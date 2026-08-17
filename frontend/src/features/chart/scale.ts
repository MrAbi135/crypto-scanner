// Price/time -> pixel mapping. Pure functions, no DOM, no React.
//
// This is the part of a chart that is actually wrong or right: an overlay drawn
// two pixels off is a zone that appears to sit above a candle it actually cuts
// through, and the whole point of S13a is that the developer can trust what
// they are looking at when verifying golden labels. So the mapping is separated
// from the rendering and tested on its own.

import type { Candle, Zone } from '@entities/market/types'

export interface Viewport {
  readonly width: number
  readonly height: number
  readonly padding: number
}

export interface PriceScale {
  readonly min: number
  readonly max: number
  /** Price -> y pixel. Inverted: SVG y grows downward, price grows upward. */
  readonly y: (price: string | number) => number
  readonly contains: (price: string | number) => boolean
}

export interface TimeScale {
  /** Candle index -> x pixel of the candle's centre. */
  readonly x: (index: number) => number
  readonly bandWidth: number
}

/** The visible price range, set by price alone.
 *
 * Zones deliberately do NOT widen it. An earlier version included them, on the
 * reasoning that a zone outside the range would silently vanish. That was the
 * wrong trade: a pool six thousand dollars away stretched the axis until three
 * hundred candles collapsed into a strip a few pixels tall, and the price
 * action — the thing the chart exists to show — became unreadable.
 *
 * Objects outside the range are clipped instead, and `clipped` reports how
 * many. Stating the number is what keeps this honest: nothing disappears
 * without the chart saying so.
 */
export function priceScale(candles: readonly Candle[], viewport: Viewport): PriceScale {
  const values: number[] = []

  for (const candle of candles) {
    values.push(Number(candle.high), Number(candle.low))
  }

  if (values.length === 0) {
    return { min: 0, max: 1, y: () => viewport.height / 2, contains: () => false }
  }

  const rawMin = Math.min(...values)
  const rawMax = Math.max(...values)

  // A flat series has zero extent; without this the scale divides by zero and
  // every candle lands on one line.
  const span = rawMax - rawMin || Math.max(Math.abs(rawMax) * 0.001, 1)

  const min = rawMin - span * 0.05
  const max = rawMax + span * 0.05

  const usable = viewport.height - viewport.padding * 2

  return {
    min,
    max,
    y: (price) => {
      const value = typeof price === 'string' ? Number(price) : price
      const ratio = (value - min) / (max - min)

      return viewport.padding + (1 - ratio) * usable
    },
    contains: (price) => {
      const value = typeof price === 'string' ? Number(price) : price

      return value >= min && value <= max
    },
  }
}

/** Objects with any part inside the visible band, and how many were dropped. */
export function visibleZones(
  zones: readonly Zone[],
  price: PriceScale,
): { shown: readonly Zone[]; clipped: number } {
  const shown = zones.filter(
    (zone) =>
      price.contains(zone.band_low) ||
      price.contains(zone.band_high) ||
      (Number(zone.band_low) < price.min && Number(zone.band_high) > price.max),
  )

  return { shown, clipped: zones.length - shown.length }
}

/** The x of the first candle at or after `iso`, or the left edge if earlier.
 *
 * Zones are positioned by time, not by `created_index`: that index is an offset
 * into whatever window the engine replayed, and the chart holds a different
 * one. Placing a zone at index 1846 of a 297-candle chart would put the object
 * somewhere it has no relation to -- worse than not placing it at all.
 */
export function xForTime(
  candles: readonly Candle[],
  iso: string,
  viewport: Viewport,
  time: TimeScale,
): number {
  const at = Date.parse(iso)

  const index = candles.findIndex((candle) => Date.parse(candle.open_time) >= at)

  // Created after this window ends: pin to the right edge rather than drop it.
  if (index === -1) return viewport.width - viewport.padding

  return index === 0 ? viewport.padding : time.x(index)
}

export function timeScale(count: number, viewport: Viewport): TimeScale {
  const usable = viewport.width - viewport.padding * 2
  const band = count > 0 ? usable / count : usable

  return {
    bandWidth: band,
    x: (index) => viewport.padding + band * (index + 0.5),
  }
}

/** Candle body geometry, with the doji case made explicit. */
export function bodyRect(
  candle: Candle,
  index: number,
  price: PriceScale,
  time: TimeScale,
): { x: number; y: number; width: number; height: number } {
  const open = price.y(candle.open)
  const close = price.y(candle.close)

  const width = Math.max(1, time.bandWidth * 0.7)

  return {
    x: time.x(index) - width / 2,
    y: Math.min(open, close),
    width,
    // A doji has open === close, which is a zero-height rect: invisible in SVG.
    // One pixel keeps it on the chart, where it belongs -- a doji is a fact
    // about the market, not a rendering edge case to drop.
    height: Math.max(1, Math.abs(close - open)),
  }
}

export function isUp(candle: Candle): boolean {
  return Number(candle.close) >= Number(candle.open)
}
