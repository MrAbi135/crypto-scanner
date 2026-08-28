import { fireEvent, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { DashboardScreen } from './DashboardScreen'
import { setSession } from '@services/api/session'
import { axeViolations, describeViolations } from '@test/axe'

const META = { generated_at: '2026-08-28T12:00:00+00:00', freshness: { state: 'RECORDED' } }

const OVERVIEW = {
  top_signals: [
    {
      rank: 1,
      signal_id: 'sig-1',
      symbol: 'ETHUSDT',
      timeframe: 'H1',
      direction: 'UP',
      archetype: 'A3',
      grade: 'B',
      confidence: '75',
      display_rank: '71.2',
      lifecycle_state: 'PUBLISHED',
    },
    {
      rank: 2,
      signal_id: 'sig-2',
      symbol: 'BTCUSDT',
      timeframe: 'H4',
      direction: 'DOWN',
      archetype: 'A1',
      grade: 'A',
      confidence: '82',
      display_rank: '80.0',
      lifecycle_state: 'ACTIVE',
    },
  ],
  live_total: 7,
  recent_sweeps: [
    {
      symbol: 'BTCUSDT',
      timeframe: 'M15',
      pool_id: 'p1',
      side: 'BSL',
      event: 'SWEPT',
      reason: 'single_candle_sweep',
      at: '2026-08-28T11:45:00+00:00',
    },
    {
      symbol: 'ETHUSDT',
      timeframe: 'M5',
      pool_id: 'p2',
      side: null,
      event: 'STOP_HUNT',
      reason: 'stop_hunt',
      at: '2026-08-28T11:40:00+00:00',
    },
  ],
  not_measured: [
    'regime ribbon — needs breadth statistics no engine computes',
    'compression — no aggregation exists',
    'watchlist pulse — needs S17’s workspace tables',
  ],
}

function stub(data: unknown = OVERVIEW, status = 200) {
  vi.stubGlobal(
    'fetch',
    vi.fn(async () =>
      Promise.resolve(
        new Response(
          JSON.stringify(
            status === 200
              ? { data, meta: META }
              : { error: { code: 'INTERNAL', message: 'boom', correlation_id: 'cid-5' } },
          ),
          { status, headers: { 'content-type': 'application/json' } },
        ),
      ),
    ),
  )
}

beforeEach(() => {
  setSession({ accessToken: 't', userId: 'u', tenantId: 't', expiresAt: Date.now() + 9e5 })
  stub()
})

afterEach(() => {
  vi.unstubAllGlobals()
  setSession(null)
})

describe('DashboardScreen', () => {
  it('gives the head its denominator', async () => {
    // Five of five and five of ninety are different markets.
    render(<DashboardScreen />)

    expect((await screen.findByTestId('dashboard-live-total')).textContent).toContain(
      '2 of 7 live',
    )
  })

  it('names the panels it cannot show, from the server’s list', async () => {
    render(<DashboardScreen />)

    const gap = await screen.findByTestId('dashboard-not-measured')

    expect(gap.textContent).toContain('regime')
    expect(gap.textContent).toContain('watchlist')
  })

  it('says a vanished side is unknown rather than guessing one', async () => {
    render(<DashboardScreen />)

    const sweeps = await screen.findByTestId('dashboard-sweeps')

    expect(sweeps.textContent).toContain('side unknown')
    expect(sweeps.textContent).toContain('BSL')
  })

  it('opens each signal’s own detail', async () => {
    const opened: string[] = []

    render(<DashboardScreen onOpenSignal={(id) => opened.push(id)} />)

    fireEvent.click(await screen.findByTestId('dashboard-open-sig-2'))

    expect(opened).toEqual(['sig-2'])
  })

  it('opens a sweep’s context on the chart', async () => {
    const opened: string[][] = []

    render(<DashboardScreen onOpenChart={(s, tf) => opened.push([s, tf])} />)

    await screen.findByTestId('dashboard-sweeps')

    fireEvent.click(screen.getByRole('button', { name: 'Open BTCUSDT M15 on the chart' }))

    expect(opened).toEqual([['BTCUSDT', 'M15']])
  })

  it('renders a quiet board as quiet, not broken', async () => {
    stub({ ...OVERVIEW, top_signals: [], live_total: 0 })

    render(<DashboardScreen />)

    expect(await screen.findByTestId('dashboard-quiet')).toBeDefined()
  })

  it('shows a failure with its correlation id', async () => {
    stub(undefined, 500)

    render(<DashboardScreen />)

    expect((await screen.findByTestId('dashboard-error')).textContent).toContain('cid-5')
  })

  it('is axe-clean', async () => {
    const { container } = render(<DashboardScreen />)

    await screen.findByTestId('dashboard-signals')

    const violations = await axeViolations(container)

    expect(violations, describeViolations(violations)).toEqual([])
  })
})
