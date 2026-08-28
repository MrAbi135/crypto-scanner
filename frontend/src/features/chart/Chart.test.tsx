import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import type { Candle, Pool, StructureEvent, Zone } from '@entities/market/types'
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
    created_at: '2026-08-17T00:00:00+00:00',
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
    render(<Chart candles={[]} zones={[]} pools={[]} structure={[]} />)

    expect(screen.getByTestId('chart-empty')).toBeDefined()
  })

  it('draws one group per candle', () => {
    render(<Chart candles={[candle(0), candle(1), candle(2)]} zones={[]} pools={[]} structure={[]} />)

    expect(screen.getByTestId('candles').children).toHaveLength(3)
  })

  it('marks direction so up and down are distinguishable without colour', () => {
    render(<Chart candles={[candle(0, '105'), candle(1, '95')]} zones={[]} pools={[]} structure={[]} />)

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
        pools={[]} structure={[]}
      />,
    )

    expect(screen.getByTestId('zone-z1').getAttribute('data-zone-type')).toBe('FVG')
    expect(screen.getByTestId('zone-z2').getAttribute('data-state')).toBe('TESTED')
  })

  it('flags a stale zone rather than drawing it like a live one', () => {
    render(<Chart candles={[candle(0)]} zones={[zone('z1', { stale_context: true })]} pools={[]} structure={[]} />)

    expect(screen.getByTestId('zone-z1').getAttribute('class')).toContain('zone--stale')
  })

  it('draws pools as levels carrying their side', () => {
    render(<Chart candles={[candle(0)]} zones={[]} pools={[pool('p1'), pool('p2', 'SSL')]} structure={[]} />)

    expect(screen.getByTestId('pool-p1').getAttribute('data-side')).toBe('BSL')
    expect(screen.getByTestId('pool-p2').getAttribute('data-side')).toBe('SSL')
  })

  it('places a zone band at the same y as the price it represents', () => {
    // The assertion that matters: an overlay two pixels off is a zone that
    // appears to sit above a candle it actually cuts through.
    render(<Chart candles={[candle(0)]} zones={[zone('z1')]} pools={[pool('p1')]} structure={[]} />)

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
    render(<Chart candles={[candle(0)]} zones={[zone('z1')]} pools={[pool('p1')]} structure={[]} />)

    // "1 of 1 in view" rather than "1 zones": when objects are clipped for
    // being outside the price range, a screen-reader user has to hear that
    // too, or the chart quietly under-reports what the engine found.
    expect(screen.getByRole('img').getAttribute('aria-label')).toBe(
      'Price chart with 1 candles, 1 of 1 zones in view, 1 liquidity pools and 0 swings',
    )
  })
})

describe('zone placement', () => {
  it('starts a zone where it was created, not at the left edge', () => {
    // Drawing a zone full-width claims it existed before the engine found it.
    // For a reader verifying a label against the SLS that is a false
    // statement, and the kind that looks like a rendering choice.
    render(
      <Chart
        candles={[candle(0), candle(1), candle(2), candle(3)]}
        zones={[zone('z1', { created_at: '2026-08-17T02:00:00+00:00' })]}
        pools={[]} structure={[]}
      />,
    )

    const band = screen.getByTestId('zone-z1')

    // Created at the third candle, so its left edge is well past the frame's.
    expect(Number(band.getAttribute('x'))).toBeGreaterThan(300)
  })

  it('runs a zone created before the window from the left edge', () => {
    // Still live, so it must appear -- dropping it would read as "the engine
    // found nothing", the one claim the chart must never make by accident.
    render(
      <Chart
        candles={[candle(1), candle(2)]}
        zones={[zone('z1', { created_at: '2020-01-01T00:00:00+00:00' })]}
        pools={[]} structure={[]}
      />,
    )

    const band = screen.getByTestId('zone-z1')

    expect(Number(band.getAttribute('x'))).toBeLessThan(50)
    expect(Number(band.getAttribute('width'))).toBeGreaterThan(1000)
  })
})

describe('clipping', () => {
  it('says how many zones sit outside the visible price range', () => {
    // Silently dropping them would under-report what the engine found, which
    // is the one thing this screen must never do.
    render(
      <Chart
        candles={[candle(0)]}
        zones={[
          zone('near', { band_low: '95', band_high: '100' }),
          zone('far', { band_low: '10', band_high: '12' }),
        ]}
        pools={[]} structure={[]}
      />,
    )

    expect(screen.getByTestId('chart-clipped').textContent).toContain('1 of 2 zones')
    expect(screen.queryByTestId('zone-far')).toBeNull()
    expect(screen.getByTestId('zone-near')).toBeDefined()
  })

  it('stays quiet when everything is in view', () => {
    render(<Chart candles={[candle(0)]} zones={[zone('z1')]} pools={[]} structure={[]} />)

    expect(screen.queryByTestId('chart-clipped')).toBeNull()
  })
})

describe('Chart swings', () => {
  function swing(
    event_type: string,
    event_at: string,
    price: string,
  ): StructureEvent {
    return {
      event_type,
      event_at,
      algo_version: 's4-v8',
      evidence: { index: 400, price, kind: 'HIGH', strength: 'EXTERNAL' },
    }
  }

  it('places a swing at the price the engine recorded', () => {
    // The chart's whole job is to be checkable against the SLS by eye, so the
    // marker has to sit on the number the engine wrote down. Asserted through
    // the same price scale the candles use rather than a hard-coded pixel: a
    // literal here would pass while the scale was wrong for everything.
    render(
      <Chart
        candles={[candle(1)]}
        zones={[]}
        pools={[]}
        structure={[swing('SWING_EXTERNAL_HIGH', '2026-08-17T01:00:00+00:00', '110')]}
      />,
    )

    const marker = screen.getByTestId('swing-SWING_EXTERNAL_HIGH-2026-08-17T01:00:00+00:00')
    const high = document.querySelector('.candle__wick') as SVGLineElement

    expect(marker.getAttribute('data-price')).toBe('110')
    // '110' is this candle's high, so the marker sits on the top of its wick.
    expect(marker.getAttribute('cy')).toBe(high.getAttribute('y1'))
  })

  it('distinguishes internal from external without hiding either', () => {
    render(
      <Chart
        candles={[candle(1), candle(2)]}
        zones={[]}
        pools={[]}
        structure={[
          swing('SWING_INTERNAL_HIGH', '2026-08-17T01:00:00+00:00', '105'),
          swing('SWING_EXTERNAL_HIGH', '2026-08-17T02:00:00+00:00', '105'),
        ]}
      />,
    )

    const drawn = document.querySelectorAll('[data-testid^="swing-"]')
    const radii = [...drawn].map((node) => node.getAttribute('r'))

    expect(drawn).toHaveLength(2)
    expect(new Set(radii).size).toBe(2)
  })

  it('names the swing in text, not only in colour', () => {
    // A marker whose meaning is carried only by fill is unreadable to anyone
    // who cannot distinguish it, and unreadable to a screen reader entirely.
    render(
      <Chart
        candles={[candle(1)]}
        zones={[]}
        pools={[]}
        structure={[swing('SWING_EXTERNAL_HIGH', '2026-08-17T01:00:00+00:00', '110')]}
      />,
    )

    expect(screen.getByText(/EXTERNAL HIGH at 110/)).toBeDefined()
  })

  it('draws nothing when the engine recorded no swings', () => {
    // Distinct from an error. A quiet window and a broken fetch must not look
    // the same, which is why the count is in the chart's own label.
    render(<Chart candles={[candle(1)]} zones={[]} pools={[]} structure={[]} />)

    expect(document.querySelectorAll('[data-testid^="swing-"]')).toHaveLength(0)
    expect(screen.getByTestId('chart').getAttribute('aria-label')).toContain('0 swings')
  })
})
