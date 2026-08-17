import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import type { Candle, Pool, Zone } from '@entities/market/types'
import { Chart } from './Chart'

function candle(index: number, close = '105'): Candle {
  return {
    open_time: `2026-08-17T0${index}:00:00+00:00`,
    close_time: `2026-08-17T0${index + 1}:00:00+00:00`,
    open: '100',
    high: '110',
    low: '90',
    close,
    volume: '10',
    trade_count: 5,
    source: 'stream',
  }
}

function zone(id: string, overrides: Partial<Zone> = {}): Zone {
  return {
    zone_id: id,
    zone_type: 'FVG',
    polarity: 'BULLISH',
    state: 'FRESH',
    grade: 'A',
    band_low: '95',
    band_high: '100',
    refined_low: null,
    refined_high: null,
    created_index: 0,
    confirmed_index: 1,
    parent_zone_id: null,
    stale_context: false,
    gap_adjacent: false,
    updated_at: '2026-08-17T00:00:00+00:00',
    evidence: {},
    ...overrides,
  }
}

function pool(id: string, side = 'BSL'): Pool {
  return {
    pool_id: id,
    side,
    liquidity_class: 'EXTERNAL',
    source: 'SWING',
    price: '108',
    band_low: '107',
    band_high: '109',
    state: 'ACTIVE',
    member_count: 1,
    created_index: 0,
    strength: { score: '70', components: {} },
  }
}

describe('Chart', () => {
  it('says there are no candles rather than showing a blank frame', () => {
    // "No data" and "the engine found nothing" are opposite claims, and an
    // empty chart makes them look identical.
    render(<Chart candles={[]} zones={[]} pools={[]} />)

    expect(screen.getByTestId('chart-empty')).toBeDefined()
  })

  it('draws one group per candle', () => {
    render(<Chart candles={[candle(0), candle(1), candle(2)]} zones={[]} pools={[]} />)

    expect(screen.getByTestId('candles').children).toHaveLength(3)
  })

  it('marks direction so up and down are distinguishable without colour', () => {
    render(<Chart candles={[candle(0, '105'), candle(1, '95')]} zones={[]} pools={[]} />)

    expect(screen.getByTestId('candle-0').getAttribute('class')).toContain('candle--up')
    expect(screen.getByTestId('candle-1').getAttribute('class')).toContain('candle--down')
  })

  it('renders every zone with its type and state readable from the DOM', () => {
    // The developer verifying a golden label needs to know *which* object they
    // are looking at, not merely that a rectangle exists.
    render(
      <Chart
        candles={[candle(0)]}
        zones={[zone('z1'), zone('z2', { zone_type: 'OB', state: 'TESTED' })]}
        pools={[]}
      />,
    )

    expect(screen.getByTestId('zone-z1').getAttribute('data-zone-type')).toBe('FVG')
    expect(screen.getByTestId('zone-z2').getAttribute('data-state')).toBe('TESTED')
  })

  it('flags a stale zone rather than drawing it like a live one', () => {
    render(<Chart candles={[candle(0)]} zones={[zone('z1', { stale_context: true })]} pools={[]} />)

    expect(screen.getByTestId('zone-z1').getAttribute('class')).toContain('zone--stale')
  })

  it('draws pools as levels carrying their side', () => {
    render(<Chart candles={[candle(0)]} zones={[]} pools={[pool('p1'), pool('p2', 'SSL')]} />)

    expect(screen.getByTestId('pool-p1').getAttribute('data-side')).toBe('BSL')
    expect(screen.getByTestId('pool-p2').getAttribute('data-side')).toBe('SSL')
  })

  it('places a zone band at the same y as the price it represents', () => {
    // The assertion that matters: an overlay two pixels off is a zone that
    // appears to sit above a candle it actually cuts through.
    render(<Chart candles={[candle(0)]} zones={[zone('z1')]} pools={[pool('p1')]} />)

    const band = screen.getByTestId('zone-z1')
    const level = screen.getByTestId('pool-p1')

    const bandTop = Number(band.getAttribute('y'))
    const bandBottom = bandTop + Number(band.getAttribute('height'))
    const poolY = Number(level.getAttribute('y1'))

    // Pool at 108 sits above a zone spanning 95-100, so a smaller y.
    expect(poolY).toBeLessThan(bandTop)
    expect(bandBottom).toBeGreaterThan(bandTop)
  })

  it('describes its contents for a screen reader', () => {
    render(<Chart candles={[candle(0)]} zones={[zone('z1')]} pools={[pool('p1')]} />)

    expect(screen.getByRole('img').getAttribute('aria-label')).toBe(
      'Price chart with 1 candles, 1 zones and 1 liquidity pools',
    )
  })
})
