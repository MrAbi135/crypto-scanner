import { render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { RankingsScreen } from './RankingsScreen'
import { setSession } from '@services/api/session'
import { axeViolations, describeViolations } from '@test/axe'

const META = {
  generated_at: '2026-08-27T01:00:00+00:00',
  freshness: { state: 'RECORDED', observed_at: '2026-08-27T00:00:00+00:00' },
}

const WEIGHTS = {
  param_set_version: '2026.08.24.2',
  factors: [
    {
      factor: 'F1',
      name: 'Structure',
      weight: '0.20',
      weight_pct: '20.00',
      justification:
        'The break is the claim and the trend is the context it is made in, which is why this row carries the largest share alongside the zone.',
    },
  ],
  grades: [
    { grade: 'S', min_confidence: '90' },
    { grade: 'A', min_confidence: '80' },
    { grade: 'B', min_confidence: '70' },
  ],
  below_lowest_floor: 'not published',
}

const RANKED = {
  rank: 1,
  symbol: 'ETHUSDT',
  timeframe: 'H1',
  direction: 'UP',
  archetype: 'A3',
  tier: 'T1',
  confidence: '75',
  display_rank: '61.5',
}

function stub(rows: unknown[], page: Record<string, unknown>, weights: unknown = WEIGHTS) {
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input)
      const body = url.includes('/rankings/weights')
        ? { data: weights, meta: META }
        : { data: rows, meta: META, page }

      return Promise.resolve(
        new Response(JSON.stringify(body), {
          status: 200,
          headers: { 'content-type': 'application/json' },
        }),
      )
    }),
  )
}

beforeEach(() => {
  setSession({
    accessToken: 'test-token',
    userId: 'u1',
    tenantId: 't1',
    expiresAt: Date.now() + 900_000,
  })
})

afterEach(() => {
  vi.unstubAllGlobals()
  setSession(null)
})

describe('RankingsScreen', () => {
  it('reports the denominator even when rows exist', async () => {
    // §8.6 keeps below-floor candidates "for calibration". A board showing
    // only its rows makes a quiet market and a broken pipeline look identical
    // — the confusion that cost days on the host, where 64 candidates scored
    // and none published. So the count is always on screen, not only when the
    // board is empty.
    stub([RANKED], { count: 1, has_more: false, gate_passers: 9, below_floor: 8 })

    render(<RankingsScreen />)

    const line = await screen.findByTestId('denominator')

    expect(line.textContent).toContain('1 published')
    expect(line.textContent).toContain('9 scored')
    expect(line.textContent).toContain('8 below the floor')
  })

  it('separates "nothing was evaluated" from "nothing qualified"', async () => {
    // Two empty boards that mean opposite things.
    stub([], { count: 0, has_more: false, gate_passers: 0, below_floor: 0 })

    render(<RankingsScreen />)

    expect((await screen.findByTestId('rankings-empty')).textContent).toContain(
      'reached the gates',
    )

    vi.unstubAllGlobals()
    stub([], { count: 0, has_more: false, gate_passers: 12, below_floor: 12 })

    render(<RankingsScreen />)

    await waitFor(() =>
      expect(
        screen.getAllByTestId('rankings-empty').some((n) => n.textContent?.includes('floor')),
      ).toBe(true),
    )
  })

  it('says so when the board reported no denominator at all', async () => {
    // Absent is not zero. Rendering "0 scored" from a missing field would be a
    // number the screen was never told.
    stub([RANKED], { count: 1, has_more: false })

    render(<RankingsScreen />)

    expect((await screen.findByTestId('denominator')).textContent).toContain(
      'did not report',
    )
  })

  it('shows both confidence numbers', async () => {
    stub([RANKED], { count: 1, has_more: false, gate_passers: 1, below_floor: 0 })

    render(<RankingsScreen />)

    const row = await screen.findByTestId('ranked-1')

    expect(row.textContent).toContain('75')
    expect(row.textContent).toContain('61.5')
  })

  it('publishes §9.1’s justification in full', async () => {
    // §18.6 calls this a "doctrine transparency endpoint", and that only means
    // something if the words are §9.1's own. A truncation here would be the
    // paraphrase the endpoint refuses to make.
    stub([RANKED], { count: 1, has_more: false, gate_passers: 1, below_floor: 0 })

    render(<RankingsScreen />)

    const panel = await screen.findByTestId('weights')

    expect(panel.textContent).toContain(WEIGHTS.factors[0]?.justification)
    expect(panel.textContent).toContain('2026.08.24.2')
  })

  it('states that below the lowest floor is not a grade', async () => {
    // §9.4. Said in the server's words so a client cannot invent a "C".
    stub([RANKED], { count: 1, has_more: false, gate_passers: 1, below_floor: 0 })

    render(<RankingsScreen />)

    const bands = await screen.findByTestId('grade-bands')

    expect(bands.textContent).toContain('not published')
  })

  it('renders the board when the weights fail, and vice versa', async () => {
    // Two panels, two failures. §7's panel discipline: one dead request must
    // not take the other's answer off the screen.
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input)

        if (url.includes('/rankings/weights')) {
          return Promise.resolve(
            new Response(
              JSON.stringify({
                error: { code: 'INTERNAL', message: 'no', correlation_id: 'cid-2' },
              }),
              { status: 500, headers: { 'content-type': 'application/json' } },
            ),
          )
        }

        return Promise.resolve(
          new Response(
            JSON.stringify({
              data: [RANKED],
              meta: META,
              page: { count: 1, has_more: false, gate_passers: 1, below_floor: 0 },
            }),
            { status: 200, headers: { 'content-type': 'application/json' } },
          ),
        )
      }),
    )

    render(<RankingsScreen />)

    expect(await screen.findByTestId('rankings-board')).toBeDefined()
    expect((await screen.findByTestId('weights-error')).textContent).toContain('cid-2')
  })

  it('is axe-clean', async () => {
    stub([RANKED], { count: 1, has_more: false, gate_passers: 1, below_floor: 0 })

    const { container } = render(<RankingsScreen />)

    await screen.findByTestId('weights')

    const violations = await axeViolations(container)

    expect(violations, describeViolations(violations)).toEqual([])
  })
})
