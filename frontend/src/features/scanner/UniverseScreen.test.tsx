import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { UniverseScreen } from './UniverseScreen'
import { setSession } from '@services/api/session'
import { axeViolations, describeViolations } from '@test/axe'

const META = { generated_at: '2026-08-28T07:00:00+00:00', freshness: { state: 'RECORDED' } }

const COLLECTING = {
  symbol: 'BTCUSDT',
  base_asset: 'BTC',
  quote_asset: 'USDT',
  status: 'QUARANTINE',
  tier: 'INELIGIBLE',
  candidate_tier: null,
  consecutive_passes: 0,
  consecutive_failures: 0,
  observation_days: 4,
  assessment: 'collecting',
  first_seen_at: '2026-08-25T00:00:00+00:00',
}

function stub(rows: unknown[], page: Record<string, unknown> = {}) {
  vi.stubGlobal(
    'fetch',
    vi.fn(async () =>
      Promise.resolve(
        new Response(
          JSON.stringify({
            data: rows,
            meta: META,
            page: { count: rows.length, has_more: false, ...page },
          }),
          { status: 200, headers: { 'content-type': 'application/json' } },
        ),
      ),
    ),
  )
}

const THRESHOLDS = { required_observation_days: 7, required_promotion_days: 7 }

beforeEach(() => {
  setSession({ accessToken: 't', userId: 'u', tenantId: 't', expiresAt: Date.now() + 9e5 })
})

afterEach(() => {
  vi.unstubAllGlobals()
  setSession(null)
})

describe('UniverseScreen', () => {
  it('gives the counters their denominator', async () => {
    // Two counters and no threshold is a number with nothing to be out of.
    // "4" and "4/7" are different facts and only one of them explains why the
    // symbol is still in quarantine.
    stub([COLLECTING], THRESHOLDS)

    render(<UniverseScreen />)

    const row = await screen.findByTestId('universe-BTCUSDT')

    expect(row.textContent).toContain('4/7')
    expect(row.textContent).toContain('0/7')
  })

  it('says so rather than inventing a denominator it was not given', async () => {
    // A page that printed "of 7" without being told 7 would be inventing the
    // rule it exists to explain.
    stub([COLLECTING])

    render(<UniverseScreen />)

    expect((await screen.findByTestId('universe-thresholds')).textContent).toContain(
      'did not report',
    )
    expect(screen.getByTestId('universe-BTCUSDT').textContent).not.toContain('/7')
  })

  it('shows the server’s assessment rather than deriving one', async () => {
    // Deriving "collecting" from the counters here would put §1.4's rule in
    // two places, one of them undocumented and free to drift.
    stub([{ ...COLLECTING, assessment: 'failing', consecutive_failures: 2 }], THRESHOLDS)

    render(<UniverseScreen />)

    expect((await screen.findByTestId('assessment-BTCUSDT')).textContent).toBe('failing')
  })

  it('shows a failure count only when there is one', async () => {
    stub([COLLECTING], THRESHOLDS)

    render(<UniverseScreen />)

    expect((await screen.findByTestId('universe-BTCUSDT')).textContent).not.toContain('failed')
  })

  it('filters on the server', async () => {
    stub([COLLECTING], THRESHOLDS)

    render(<UniverseScreen />)

    await screen.findByTestId('universe-board')

    fireEvent.click(screen.getByTestId('universe-status-ACTIVE'))

    await waitFor(() => {
      const calls = (globalThis.fetch as unknown as { mock: { calls: unknown[][] } }).mock.calls

      expect(decodeURIComponent(String(calls[calls.length - 1]?.[0]))).toContain(
        'filter[status]=ACTIVE',
      )
    })
  })

  it('says an empty status is empty, not broken', async () => {
    stub([], THRESHOLDS)

    render(<UniverseScreen />)

    expect(await screen.findByTestId('universe-empty')).toBeDefined()
  })

  it('is axe-clean', async () => {
    stub([COLLECTING], THRESHOLDS)

    const { container } = render(<UniverseScreen />)

    await screen.findByTestId('universe-board')

    const violations = await axeViolations(container)

    expect(violations, describeViolations(violations)).toEqual([])
  })
})
