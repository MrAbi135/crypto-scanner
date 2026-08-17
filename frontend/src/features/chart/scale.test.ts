import { describe, expect, it } from 'vitest'

import type { Candle, Zone } from '@entities/market/types'
import { bodyRect, isUp, priceScale, timeScale, type Viewport } from './scale'

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

  it('includes zones in the extent so an overlay cannot fall off the chart', () => {
    // A zone drawn outside the visible range simply vanishes, and a missing
    // overlay reads as "the engine found nothing" -- a different claim
    // entirely, and the one thing this screen must never say by accident.
    const far = zone('50', '55')
    const scale = priceScale([candle()], VIEWPORT, [far])

    const y = scale.y('52')

    expect(y).toBeGreaterThanOrEqual(0)
    expect(y).toBeLessThanOrEqual(VIEWPORT.height)
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
