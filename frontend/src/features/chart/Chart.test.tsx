import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import type { Candle, Pool, StructureEvent, Sweep, Zone } from '@entities/market/types'
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
    render(<Chart candles={[]} zones={[]} pools={[]} structure={[]} sweeps={[]} />)

    expect(screen.getByTestId('chart-empty')).toBeDefined()
  })

  it('draws one group per candle', () => {
    render(<Chart candles={[candle(0), candle(1), candle(2)]} zones={[]} pools={[]} structure={[]} sweeps={[]} />)

    expect(screen.getByTestId('candles').children).toHaveLength(3)
  })

  it('marks direction so up and down are distinguishable without colour', () => {
    render(<Chart candles={[candle(0, '105'), candle(1, '95')]} zones={[]} pools={[]} structure={[]} sweeps={[]} />)

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
        pools={[]} structure={[]} sweeps={[]}
      />,
    )

    expect(screen.getByTestId('zone-z1').getAttribute('data-zone-type')).toBe('FVG')
    expect(screen.getByTestId('zone-z2').getAttribute('data-state')).toBe('TESTED')
  })

  it('flags a stale zone rather than drawing it like a live one', () => {
    render(<Chart candles={[candle(0)]} zones={[zone('z1', { stale_context: true })]} pools={[]} structure={[]} sweeps={[]} />)

    expect(screen.getByTestId('zone-z1').getAttribute('class')).toContain('zone--stale')
  })

  it('draws pools as levels carrying their side', () => {
    render(<Chart candles={[candle(0)]} zones={[]} pools={[pool('p1'), pool('p2', 'SSL')]} structure={[]} sweeps={[]} />)

    expect(screen.getByTestId('pool-p1').getAttribute('data-side')).toBe('BSL')
    expect(screen.getByTestId('pool-p2').getAttribute('data-side')).toBe('SSL')
  })

  it('places a zone band at the same y as the price it represents', () => {
    // The assertion that matters: an overlay two pixels off is a zone that
    // appears to sit above a candle it actually cuts through.
    render(<Chart candles={[candle(0)]} zones={[zone('z1')]} pools={[pool('p1')]} structure={[]} sweeps={[]} />)

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
    render(<Chart candles={[candle(0)]} zones={[zone('z1')]} pools={[pool('p1')]} structure={[]} sweeps={[]} />)

    // "1 of 1 in view" rather than "1 zones": when objects are clipped for
    // being outside the price range, a screen-reader user has to hear that
    // too, or the chart quietly under-reports what the engine found.
    expect(screen.getByRole('group').getAttribute('aria-label')).toBe(
      'Price chart with 1 candles, 1 of 1 zones in view, 1 liquidity pools, 0 swings and 0 sweeps',
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
        pools={[]} structure={[]} sweeps={[]}
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
        pools={[]} structure={[]} sweeps={[]}
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
        pools={[]} structure={[]} sweeps={[]}
      />,
    )

    expect(screen.getByTestId('chart-clipped').textContent).toContain('1 of 2 zones')
    expect(screen.queryByTestId('zone-far')).toBeNull()
    expect(screen.getByTestId('zone-near')).toBeDefined()
  })

  it('stays quiet when everything is in view', () => {
    render(<Chart candles={[candle(0)]} zones={[zone('z1')]} pools={[]} structure={[]} sweeps={[]} />)

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
        sweeps={[]}
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
        sweeps={[]}
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
        sweeps={[]}
      />,
    )

    expect(screen.getByText(/EXTERNAL HIGH at 110/)).toBeDefined()
  })

  it('draws nothing when the engine recorded no swings', () => {
    // Distinct from an error. A quiet window and a broken fetch must not look
    // the same, which is why the count is in the chart's own label.
    render(<Chart candles={[candle(1)]} zones={[]} pools={[]} structure={[]} sweeps={[]} />)

    expect(document.querySelectorAll('[data-testid^="swing-"]')).toHaveLength(0)
    expect(screen.getByTestId('chart').getAttribute('aria-label')).toContain('0 swings')
  })
})

describe('Chart sweeps', () => {
  function sweep(overrides: Partial<Sweep> = {}, evidence: Record<string, unknown> = {}): Sweep {
    return {
      pool_id: 'p1',
      from_state: 'ACTIVE',
      to_state: 'SWEPT',
      reason: 'liquidity_sweep',
      transitioned_at: '2026-08-17T01:00:00+00:00',
      candle_index: 400,
      evidence: {
        reference_level: '105',
        penetration_price: '110',
        side: 'BSL',
        reclaimed: false,
        sweep_depth_atr: '0.5',
        ...evidence,
      },
      ...overrides,
    }
  }

  it('draws the reach from the level to the penetration', () => {
    // §4.6's sweep *is* the reach past the level. A dot on the level would be
    // a touch, which is the one thing §4.6 spends its length separating this
    // from -- so the two ends are asserted against the price scale, not the
    // presence of a shape.
    render(
      <Chart candles={[candle(1)]} zones={[]} pools={[]} structure={[]} sweeps={[sweep()]} />,
    )

    const group = screen.getByTestId('sweep-p1-2026-08-17T01:00:00+00:00')
    const reach = group.querySelector('.sweep__reach') as SVGLineElement
    const wick = document.querySelector('.candle__wick') as SVGLineElement

    // '110' is the candle's high and '105' sits between high and low, so the
    // penetration end lands on the wick's top and the level end does not.
    expect(reach.getAttribute('y2')).toBe(wick.getAttribute('y1'))
    expect(reach.getAttribute('y1')).not.toBe(reach.getAttribute('y2'))
  })

  it('marks a reclaimed sweep as the contrary evidence it is', () => {
    // §4.6: a reclaimed sweep argues against the setup. It is identical in
    // geometry to one that held, so nothing but an explicit mark separates
    // them.
    render(
      <Chart
        candles={[candle(1)]}
        zones={[]}
        pools={[]}
        structure={[]}
        sweeps={[sweep({}, { reclaimed: true })]}
      />,
    )

    const group = screen.getByTestId('sweep-p1-2026-08-17T01:00:00+00:00')

    expect(group.getAttribute('data-reclaimed')).toBe('true')
    expect(group.getAttribute('class')).toContain('sweep--reclaimed')
    expect(screen.getByText(/reclaimed, contrary evidence/)).toBeDefined()
  })

  it('does not draw a pool transition that is not a sweep', () => {
    // A pool also goes BROKEN and EXPIRED. Either rendered as a sweep would be
    // a confident lie on the instrument used to verify the doctrine.
    render(
      <Chart
        candles={[candle(1)]}
        zones={[]}
        pools={[]}
        structure={[]}
        sweeps={[sweep({ reason: 'two_candle_rejection_failed', to_state: 'BROKEN' })]}
      />,
    )

    expect(document.querySelectorAll('[data-testid^="sweep-"]')).toHaveLength(0)
  })

  it('skips a transition missing one of its two prices', () => {
    // A segment with one end guessed is worse than an absent one: it looks
    // measured.
    render(
      <Chart
        candles={[candle(1)]}
        zones={[]}
        pools={[]}
        structure={[]}
        sweeps={[sweep({}, { penetration_price: undefined })]}
      />,
    )

    expect(document.querySelectorAll('[data-testid^="sweep-"]')).toHaveLength(0)
  })
})

describe('Chart inspection', () => {
  const swingEvent: StructureEvent = {
    event_type: 'SWING_EXTERNAL_HIGH',
    event_at: '2026-08-17T01:00:00+00:00',
    algo_version: 's4-v8',
    evidence: { price: '110', index: 400 },
  }

  function withInspector(onInspect: () => void, selectedId: string | null = null) {
    return render(
      <Chart
        candles={[candle(1)]}
        zones={[zone('z1')]}
        pools={[]}
        structure={[swingEvent]}
        sweeps={[]}
        onInspect={onInspect}
        selectedId={selectedId}
      />,
    )
  }

  it('opens the evidence from the keyboard, not only the mouse', () => {
    // These are SVG shapes: not focusable and not announced unless made so.
    // S13a's DoD asks for an axe-clean screen, and an inspector only a mouse
    // can reach is not one.
    const onInspect = vi.fn()

    withInspector(onInspect)

    const marker = screen.getByTestId('zone-z1')

    expect(marker.getAttribute('tabindex')).toBe('0')
    expect(marker.getAttribute('role')).toBe('button')

    fireEvent.keyDown(marker, { key: 'Enter' })
    fireEvent.keyDown(marker, { key: ' ' })

    expect(onInspect).toHaveBeenCalledTimes(2)
  })

  it('ignores a key that is not an activation', () => {
    const onInspect = vi.fn()

    withInspector(onInspect)

    fireEvent.keyDown(screen.getByTestId('zone-z1'), { key: 'ArrowRight' })

    expect(onInspect).not.toHaveBeenCalled()
  })

  it('hands over the object that was activated', () => {
    const onInspect = vi.fn()

    withInspector(onInspect)

    fireEvent.click(screen.getByTestId(`swing-${swingEvent.event_type}-${swingEvent.event_at}`))

    expect(onInspect).toHaveBeenCalledWith(
      expect.objectContaining({ kind: 'swing', id: expect.stringContaining('swing:') }),
    )
  })

  it('shows the selection as well as announcing it', () => {
    // `aria-pressed` alone reaches a screen reader and nobody else. An earlier
    // draft set the class inside the shared props helper, where each overlay's
    // own className -- written after the spread -- silently overwrote it.
    withInspector(vi.fn(), 'zone:z1')

    const marker = screen.getByTestId('zone-z1')

    expect(marker.getAttribute('aria-pressed')).toBe('true')
    expect(marker.getAttribute('class')).toContain('is-selected')
  })

  it('is inert when no inspector is wired', () => {
    // The chart is still a chart without one, and a focusable shape that does
    // nothing on activation is worse than a plain one.
    render(
      <Chart candles={[candle(1)]} zones={[zone('z1')]} pools={[]} structure={[]} sweeps={[]} />,
    )

    const marker = screen.getByTestId('zone-z1')

    expect(marker.getAttribute('tabindex')).toBeNull()
    expect(marker.getAttribute('role')).toBeNull()
  })
})

describe('Chart zone states', () => {
  it('carries the state so the stylesheet can treat it', () => {
    render(
      <Chart
        candles={[candle(1)]}
        zones={[zone('z1', { state: 'MITIGATED' })]}
        pools={[]}
        structure={[]}
        sweeps={[]}
      />,
    )

    expect(screen.getByTestId('zone-z1').getAttribute('data-state')).toBe('MITIGATED')
  })

  it('marks a state it has no treatment for instead of drawing it as fresh', () => {
    // The failure this guards: an unknown state matches no rule, inherits the
    // base `.zone` wash -- which is the FRESH treatment -- and the chart quietly
    // asserts a zone is untouched. That is what shipped before §16.3 was
    // implemented, and it comes back on its own the next time the server grows
    // a state.
    render(
      <Chart
        candles={[candle(1)]}
        zones={[zone('z1', { state: 'SOMETHING_NEW' })]}
        pools={[]}
        structure={[]}
        sweeps={[]}
      />,
    )

    expect(screen.getByTestId('zone-z1').getAttribute('class')).toContain('zone--untreated')
  })

  it('does not mark a state it does know', () => {
    render(
      <Chart
        candles={[candle(1)]}
        zones={[zone('z1', { state: 'TOUCHED' })]}
        pools={[]}
        structure={[]}
        sweeps={[]}
      />,
    )

    expect(screen.getByTestId('zone-z1').getAttribute('class')).not.toContain('untreated')
  })

  it('counts untreated zones in the label a screen reader hears', () => {
    // Otherwise the loud outline is visible only to someone looking at it, and
    // the chart's own description says everything is fine.
    render(
      <Chart
        candles={[candle(1)]}
        zones={[zone('z1', { state: 'SOMETHING_NEW' })]}
        pools={[]}
        structure={[]}
        sweeps={[]}
      />,
    )

    expect(screen.getByTestId('chart').getAttribute('aria-label')).toContain('no treatment for')
  })

  it('says nothing about treatments when every state is known', () => {
    render(
      <Chart
        candles={[candle(1)]}
        zones={[zone('z1', { state: 'FRESH' })]}
        pools={[]}
        structure={[]}
        sweeps={[]}
      />,
    )

    expect(screen.getByTestId('chart').getAttribute('aria-label')).not.toContain('treatment')
  })
})
