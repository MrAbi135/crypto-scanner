import { describe, expect, it } from 'vitest'

import type { Candle, Zone } from '@entities/market/types'
import { bodyRect, isUp, priceScale, timeScale, visibleZones, type Viewport } from './scale'

const VIEWPORT: Viewport = { width: 1000, height: 500, padding: 20 }

function candle(overrides: Partial<Candle> = {}): Candle {
  return {
    open_time: '2026-08-17T00:00:00+00:00',
    close_time: '2026-08-17T01:00:00+00:00',
    open: '100',
    high: '110',
    low: '90',
    close: '105',
    volume: '10',
    trade_count: 5,
    source: 'stream',
    ...overrides,
  }
}

function zone(low: string, high: string): Zone {
  return {
    zone_id: 'z',
    zone_type: 'FVG',
    polarity: 'BULLISH',
    state: 'FRESH',
    grade: 'A',
    band_low: low,
    band_high: high,
    refined_low: null,
    refined_high: null,
    created_at: '2026-08-17T00:00:00+00:00',
    created_index: 0,
    confirmed_index: 1,
    parent_zone_id: null,
    stale_context: false,
    gap_adjacent: false,
    updated_at: '2026-08-17T00:00:00+00:00',
    evidence: {},
  }
}

describe('priceScale', () => {
  it('puts a higher price above a lower one', () => {
    // SVG y grows downward. Getting this backwards renders a mirror image of
    // the market, which looks plausible and is completely wrong.
    const scale = priceScale([candle()], VIEWPORT)

    expect(scale.y('110')).toBeLessThan(scale.y('90'))
  })

  it('keeps every candle inside the frame', () => {
    const scale = priceScale([candle()], VIEWPORT)

    expect(scale.y('110')).toBeGreaterThanOrEqual(0)
    expect(scale.y('90')).toBeLessThanOrEqual(VIEWPORT.height)
  })

  it('is set by price alone, so a distant zone cannot squash the candles', () => {
    // An earlier version widened the extent to fit every zone. A pool far from
    // price then stretched the axis until 300 candles collapsed into a strip a
    // few pixels tall -- the price action, which is the point, became
    // unreadable. Distant objects are clipped and counted instead.
    const scale = priceScale([candle()], VIEWPORT)

    expect(scale.contains('52')).toBe(false)
    expect(scale.contains('100')).toBe(true)
  })

  it('reports what it clipped rather than dropping it silently', () => {
    const scale = priceScale([candle()], VIEWPORT)

    const { shown, clipped } = visibleZones([zone('95', '100'), zone('50', '55')], scale)

    expect(shown).toHaveLength(1)
    expect(clipped).toBe(1)
  })

  it('keeps a zone that straddles the whole visible range', () => {
    // Band wider than the window on both sides: neither edge is inside, but the
    // zone covers every candle, so dropping it would hide the most relevant
    // object on the chart.
    const scale = priceScale([candle()], VIEWPORT)

    expect(visibleZones([zone('10', '500')], scale).shown).toHaveLength(1)
  })

  it('survives a flat series without collapsing every candle onto one line', () => {
    const flat = [candle({ open: '100', high: '100', low: '100', close: '100' })]

    const scale = priceScale(flat, VIEWPORT)

    expect(Number.isFinite(scale.y('100'))).toBe(true)
    expect(scale.max).toBeGreaterThan(scale.min)
  })

  it('centres an empty series rather than dividing by zero', () => {
    const scale = priceScale([], VIEWPORT)

    expect(scale.y('123')).toBe(VIEWPORT.height / 2)
  })
})

describe('timeScale', () => {
  it('spaces candles evenly across the usable width', () => {
    const scale = timeScale(4, VIEWPORT)

    const gaps = [scale.x(1) - scale.x(0), scale.x(2) - scale.x(1), scale.x(3) - scale.x(2)]

    expect(new Set(gaps.map((g) => g.toFixed(6))).size).toBe(1)
  })

  it('keeps the first and last candle inside the padding', () => {
    const scale = timeScale(10, VIEWPORT)

    expect(scale.x(0)).toBeGreaterThan(VIEWPORT.padding)
    expect(scale.x(9)).toBeLessThan(VIEWPORT.width - VIEWPORT.padding)
  })
})

describe('bodyRect', () => {
  it('draws a doji as a visible line rather than nothing', () => {
    // open === close is a zero-height rect, which SVG renders as absent. A
    // doji is a fact about the market, not a rendering edge case to drop.
    const doji = candle({ open: '100', close: '100' })

    const price = priceScale([doji], VIEWPORT)
    const time = timeScale(1, VIEWPORT)

    expect(bodyRect(doji, 0, price, time).height).toBeGreaterThanOrEqual(1)
  })

  it('anchors the body between open and close regardless of direction', () => {
    const down = candle({ open: '105', close: '100' })

    const price = priceScale([down], VIEWPORT)
    const time = timeScale(1, VIEWPORT)

    const rect = bodyRect(down, 0, price, time)

    expect(rect.y).toBeCloseTo(price.y('105'), 5)
    expect(rect.y + rect.height).toBeCloseTo(price.y('100'), 5)
  })
})

describe('isUp', () => {
  it('treats an unchanged close as up rather than down', () => {
    expect(isUp(candle({ open: '100', close: '100' }))).toBe(true)
    expect(isUp(candle({ open: '100', close: '99' }))).toBe(false)
  })
})
