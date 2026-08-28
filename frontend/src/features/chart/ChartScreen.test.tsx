// The whole screen, from a stubbed API to a placed overlay.
//
// **This is not the browser E2E S13a's Testing section asks for.** That one
// wants a real browser against a running API, and it would catch what jsdom
// cannot: focus order under real layout, colour contrast from a stylesheet
// nothing here applies, an SVG that renders at the wrong size. Playwright is
// the tool and it is not installed; this file does not stand in for it and the
// sprint should not be read as closed because of it.
//
// What it does prove is the assertion S13a names -- a known window loads and a
// known object renders at a known price -- across the real path: fetch, parse,
// shape, scale, draw. Every layer between the wire and the pixel is the real
// one, which is where the mistakes in this feature have actually been.

import { render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { ChartScreen } from './ChartScreen'
import { setSession } from '@services/api/session'
import { axeViolations, describeViolations } from '@test/axe'

// One hour of BTCUSDT H1, with one of everything the chart draws. Prices are
// chosen so each object lands somewhere distinguishable rather than on top of
// the others.
const CANDLES = [
  {
    open_time: '2026-08-17T01:00:00+00:00',
    close_time: '2026-08-17T02:00:00+00:00',
    open: '100',
    high: '120',
    low: '90',
    close: '110',
    volume: '10',
    trade_count: 5,
    source: 'stream',
  },
]

const ZONE = {
  zone_id: 'z1',
  zone_type: 'OB',
  polarity: 'BULLISH',
  state: 'FRESH',
  grade: 'OB_A',
  band_low: '100',
  band_high: '105',
  refined_low: null,
  refined_high: null,
  created_at: '2026-08-17T01:00:00+00:00',
  created_index: 400,
  confirmed_index: 402,
  parent_zone_id: null,
  stale_context: false,
  gap_adjacent: false,
  updated_at: '2026-08-17T01:00:00+00:00',
  evidence: { mss_origin: true },
}

const POOL = {
  pool_id: 'p1',
  side: 'BSL',
  liquidity_class: 'EXTERNAL',
  source: 'swing',
  price: '118',
  band_low: '117',
  band_high: '119',
  state: 'ACTIVE',
  member_count: 2,
  created_index: 400,
  strength: { score: '23.75', components: { touches: 2 } },
}

const SWEEP = {
  pool_id: 'p1',
  from_state: 'ACTIVE',
  to_state: 'SWEPT',
  reason: 'liquidity_sweep',
  transitioned_at: '2026-08-17T01:00:00+00:00',
  candle_index: 400,
  evidence: {
    reference_level: '118',
    penetration_price: '120',
    side: 'BSL',
    reclaimed: false,
    sweep_depth_atr: '0.4',
  },
}

const SWING = {
  event_type: 'SWING_EXTERNAL_HIGH',
  event_at: '2026-08-17T01:00:00+00:00',
  algo_version: 's4-v8',
  evidence: { price: '120', index: 400, kind: 'HIGH', strength: 'EXTERNAL' },
}

const META = {
  generated_at: '2026-08-17T02:00:00+00:00',
  freshness: { state: 'RECORDED', observed_at: '2026-08-17T02:00:00+00:00' },
  versions: { algo_version: 's4-v8', param_set_version: '2026.08.24.2' },
}

function envelope(data: unknown) {
  return { data, meta: META, page: { count: 1, has_more: false } }
}

function respond(url: string) {
  if (url.includes('/market/candles')) return envelope(CANDLES)
  if (url.includes('/zones')) return envelope([ZONE])
  if (url.includes('/liquidity')) return envelope({ pools: [POOL], sweeps: [SWEEP] })
  if (url.includes('/structure')) return envelope([SWING])

  throw new Error(`unstubbed request: ${url}`)
}

beforeEach(() => {
  // The read rows require a bearer token, so the screen shows sign-in without
  // one. These tests are about the chart, so they start already signed in.
  setSession({
    accessToken: 'test-token',
    userId: 'u1',
    tenantId: 't1',
    expiresAt: Date.now() + 900_000,
  })

  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL) =>
      Promise.resolve(
        new Response(JSON.stringify(respond(String(input))), {
          status: 200,
          headers: { 'content-type': 'application/json' },
        }),
      ),
    ),
  )
})

afterEach(() => {
  vi.unstubAllGlobals()
  setSession(null)
})

describe('ChartScreen', () => {
  it('renders a known object at a known price', async () => {
    // S13a's stated assertion. The swing is at 120, which is this candle's
    // high, so it must land on the top of the wick -- compared against the
    // wick's own coordinate rather than a literal, because a literal would
    // pass while the price scale was wrong for the entire chart.
    render(<ChartScreen />)

    const marker = await screen.findByTestId(
      'swing-SWING_EXTERNAL_HIGH-2026-08-17T01:00:00+00:00',
    )
    const wick = document.querySelector('.candle__wick') as SVGLineElement

    expect(marker.getAttribute('data-price')).toBe('120')
    expect(marker.getAttribute('cy')).toBe(wick.getAttribute('y1'))
  })

  it('draws all four overlays from one load', async () => {
    render(<ChartScreen />)

    await screen.findByTestId('chart')

    expect(screen.getByTestId('zone-z1')).toBeDefined()
    expect(screen.getByTestId('pool-p1')).toBeDefined()
    expect(screen.getByTestId('sweep-p1-2026-08-17T01:00:00+00:00')).toBeDefined()
    expect(
      screen.getByTestId('swing-SWING_EXTERNAL_HIGH-2026-08-17T01:00:00+00:00'),
    ).toBeDefined()
  })

  it('opens the evidence for the object that was activated', async () => {
    render(<ChartScreen />)

    const zone = await screen.findByTestId('zone-z1')

    zone.dispatchEvent(new MouseEvent('click', { bubbles: true }))

    await waitFor(() => expect(screen.getByTestId('evidence')).toBeDefined())

    const panel = screen.getByTestId('evidence')

    expect(panel.getAttribute('data-kind')).toBe('zone')
    expect(panel.textContent).toContain('mss_origin')
    // The band as the API sent it, unrounded.
    expect(panel.textContent).toContain('100 – 105')
  })

  it('is axe-clean once the doctrine is on screen', async () => {
    // S13a's DoD. Run after the load rather than on the empty frame: the
    // overlays are the part that had to be made focusable and named, so
    // checking before they exist would check the easy half.
    const { container } = render(<ChartScreen />)

    await screen.findByTestId('chart')

    const violations = await axeViolations(container)

    expect(violations, describeViolations(violations)).toEqual([])
  })

  it('is axe-clean with the evidence panel open', async () => {
    // The panel is a live region with a table in it, and it appears after the
    // fact. Both are shapes axe has rules about.
    const { container } = render(<ChartScreen />)

    const zone = await screen.findByTestId('zone-z1')

    zone.dispatchEvent(new MouseEvent('click', { bubbles: true }))

    await waitFor(() => expect(screen.getByTestId('evidence')).toBeDefined())

    const violations = await axeViolations(container)

    expect(violations, describeViolations(violations)).toEqual([])
  })

  it('sends the bearer token the read rows require', async () => {
    // Without this every overlay is a 401, which is how the chart came to be
    // built, tested and merged while being unusable against the real API.
    render(<ChartScreen />)

    await screen.findByTestId('chart')

    const calls = (globalThis.fetch as unknown as { mock: { calls: unknown[][] } }).mock.calls
    const init = calls[0]?.[1] as RequestInit

    expect((init.headers as Record<string, string>).Authorization).toBe('Bearer test-token')
  })

  it('does not call the API at all when signed out', async () => {
    // Fetching first and rendering the 401 would report a failure the reader
    // has not caused yet.
    setSession(null)

    render(<ChartScreen />)

    expect(screen.getByTestId('signin')).toBeDefined()
    expect(globalThis.fetch).not.toHaveBeenCalled()
  })
})
