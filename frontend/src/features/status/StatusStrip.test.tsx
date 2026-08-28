import { fireEvent, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { StatusStrip } from './StatusStrip'
import { setSession } from '@services/api/session'
import { axeViolations, describeViolations } from '@test/axe'

const META = { generated_at: '2026-08-28T12:00:00+00:00', freshness: { state: 'RECORDED' } }

const NOT_MEASURED = [
  'last_scan_cycle_ms — lives in the engine process',
  'storm_mode — lives in the engine process',
]

const HEALTHY = {
  feeds: [
    {
      symbol: 'BTCUSDT',
      timeframe: 'H1',
      coverage: 'AWAITING_CLOSE',
      newest_close: '2026-08-28T12:00:00+00:00',
      candles_behind: 0,
    },
  ],
  behind_count: 0,
  degraded: [],
  degraded_count: 0,
  not_measured: NOT_MEASURED,
}

function stub(data: unknown, status = 200) {
  vi.stubGlobal(
    'fetch',
    vi.fn(async () =>
      Promise.resolve(
        new Response(JSON.stringify(status === 200 ? { data, meta: META } : data), {
          status,
          headers: { 'content-type': 'application/json' },
        }),
      ),
    ),
  )
}

beforeEach(() => {
  setSession({ accessToken: 't', userId: 'u', tenantId: 't', expiresAt: Date.now() + 9e5 })
})

afterEach(() => {
  vi.unstubAllGlobals()
  setSession(null)
})

describe('StatusStrip', () => {
  it('says what it could not measure rather than showing two of four', async () => {
    // The whole reason the strip exists. §18.3 asks for four things and this
    // endpoint can answer two; a strip that showed the two would read as a
    // strip that checked all four.
    stub(HEALTHY)

    render(<StatusStrip />)

    expect((await screen.findByTestId('status-not-measured')).textContent).toContain(
      'storm_mode',
    )
  })

  it('does not report health it failed to read', async () => {
    // The failure that matters: a fetch that 500s must not render as "all
    // feeds covered", which is what a `?? 0` on behind_count would produce.
    stub({ error: { code: 'INTERNAL', message: 'boom', correlation_id: 'cid-1' } }, 500)

    render(<StatusStrip />)

    const strip = await screen.findByTestId('status-strip')

    expect(strip.textContent).toContain('unknown')
    expect(strip.textContent).toContain('cid-1')
    expect(strip.textContent).not.toContain('covered')
  })

  it('does not read a body it does not understand as an empty one', async () => {
    // A 200 with the wrong shape is the failure that survives every error
    // handler. Destructuring it produces `behind_count: undefined`, and the
    // nearest fix -- `?? 0` -- renders a green "all 0 feeds covered" strip out
    // of a response nobody understood.
    stub({ status: 'ok' })

    render(<StatusStrip />)

    const strip = await screen.findByTestId('status-strip')

    expect(strip.textContent).toContain('unknown')
    expect(strip.textContent).not.toContain('covered')
  })

  // Each field individually, not one body missing all of them: a guard that
  // checked only `feeds` would pass a whole-body test and still crash on a
  // response whose `behind_count` went missing.
  it.each(['feeds', 'degraded', 'not_measured', 'behind_count', 'degraded_count'])(
    'treats a body missing %s as unread',
    async (field) => {
      const body: Record<string, unknown> = { ...HEALTHY }

      delete body[field]

      stub(body)

      render(<StatusStrip />)

      expect((await screen.findByTestId('status-strip')).textContent).toContain('unknown')
    },
  )

  it('gives the behind count its denominator', async () => {
    // "3 behind" out of 4 feeds and out of 4000 are different emergencies.
    stub({
      ...HEALTHY,
      feeds: [
        ...HEALTHY.feeds,
        {
          symbol: 'ETHUSDT',
          timeframe: 'H1',
          coverage: 'BEHIND',
          newest_close: '2026-08-28T06:00:00+00:00',
          candles_behind: 5,
        },
      ],
      behind_count: 1,
    })

    render(<StatusStrip />)

    expect((await screen.findByTestId('status-behind')).textContent).toBe('1 of 2 feeds behind')
  })

  it('names which feed and how far behind, on request', async () => {
    stub({
      ...HEALTHY,
      feeds: [
        {
          symbol: 'ETHUSDT',
          timeframe: 'H1',
          coverage: 'BEHIND',
          newest_close: '2026-08-28T06:00:00+00:00',
          candles_behind: 5,
        },
      ],
      behind_count: 1,
    })

    render(<StatusStrip />)

    fireEvent.click(await screen.findByTestId('status-toggle'))

    expect(screen.getByTestId('status-detail').textContent).toContain('ETHUSDT H1 — behind by 5')
  })

  it('offers no detail toggle when there is nothing to detail', async () => {
    stub(HEALTHY)

    render(<StatusStrip />)

    // Awaited on the loaded content, not on the strip: the loading placeholder
    // carries the same testid and has no toggle either, so asserting against
    // it would pass without the component ever rendering a status.
    await screen.findByTestId('status-not-measured')

    expect(screen.queryByTestId('status-toggle')).toBeNull()
  })

  it('lists open incidents', async () => {
    stub({
      ...HEALTHY,
      degraded: [
        {
          id: 'i1',
          type: 'GAP',
          symbol: 'ETHUSDT',
          timeframe: 'H1',
          started_at: '2026-08-28T10:00:00+00:00',
          candle_span: 3,
        },
      ],
      degraded_count: 1,
    })

    render(<StatusStrip />)

    expect((await screen.findByTestId('status-degraded')).textContent).toBe('1 open incident')

    fireEvent.click(screen.getByTestId('status-toggle'))

    expect(screen.getByTestId('status-detail').textContent).toContain('GAP')
  })

  it('is axe-clean', async () => {
    stub(HEALTHY)

    const { container } = render(<StatusStrip />)

    await screen.findByTestId('status-not-measured')

    const violations = await axeViolations(container)

    expect(violations, describeViolations(violations)).toEqual([])
  })
})
