import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { SignalScreen } from './SignalScreen'
import { setSession } from '@services/api/session'
import { axeViolations, describeViolations } from '@test/axe'

const META = { generated_at: '2026-08-28T12:00:00+00:00', freshness: { state: 'RECORDED' } }

// The live ETHUSDT signal's real shapes, abbreviated — not invented ones. The
// backend fixture drift this screen sits on top of (an `evidence` key the
// sealer never wrote) is exactly the failure a hand-invented shape reproduces.
const DETAIL = {
  signal_id: 'sig-1',
  symbol: 'ETHUSDT',
  timeframe: 'H1',
  direction: 'UP',
  archetype: 'A3',
  grade: 'B',
  confidence: '75',
  entry: { proximal: '2483.52', distal: '2468.00' },
  invalidation: '2468.00',
  targets: {
    primary: { low: '2515.43', high: '2515.43', pool_id: 'p-1', strength: '23.75' },
    secondary: null,
  },
  published_at: '2026-08-26T23:00:00+00:00',
  ttl_candles: 24,
  lifecycle_state: 'SUCCESS',
  versions: { algo_version: 's8-confluence-v22', param_set_version: '2026.08.24.2' },
  outcome: {
    outcome: 'SUCCESS',
    resolved_at: '2026-08-27T08:00:00+00:00',
    elapsed_candles: 9,
    mfe_r: '9.159',
    mae_r: '0',
  },
  payload: { risk: { r_multiple: '5.112' } },
  payload_hash: 'abcdef0123456789abcdef',
  payload_hash_verified: true,
}

const EVIDENCE = {
  signal_id: 'sig-1',
  symbol: 'ETHUSDT',
  timeframe: 'H1',
  evidence_ids: ['zone-abc'],
  entry_zone_id: 'zone-abc',
  confidence: {
    final: '75',
    grade: 'B',
    factors: { F1: '70', F2: '56.25', F3: '80', F4: '50', F5: '62.93', F6: '100' },
  },
  reason: 'A3 long: trend with a displaced break, retraced into the zone.',
  htf_chain: { H1: 'SIGNAL', HTF: 'UP' },
  risk: { r_multiple: '5.112' },
}

const TRANSITIONS = [
  {
    from_state: null,
    to_state: 'PUBLISHED',
    at_candle_open_time: '2026-08-26T23:00:00+00:00',
    recorded_at: '2026-08-27T00:00:01+00:00',
    stress_test: false,
    refresh: false,
    evidence: {},
  },
  {
    from_state: 'PUBLISHED',
    to_state: 'ACTIVE',
    at_candle_open_time: '2026-08-27T01:00:00+00:00',
    recorded_at: '2026-08-27T02:00:01+00:00',
    stress_test: true,
    refresh: false,
    evidence: {},
  },
]

const WEIGHTS = {
  param_set_version: '2026.08.24.2',
  factors: [
    {
      factor: 'F3',
      name: 'Liquidity context',
      weight: '0.20',
      weight_pct: '20',
      justification: 'because',
    },
  ],
  grades: [],
  below_lowest_floor: 'not published',
}

interface StubPlan {
  readonly detail?: unknown
  readonly evidence?: unknown
  readonly transitions?: unknown
  readonly failEvidence?: boolean
  readonly failTransitions?: boolean
  readonly failWeights?: boolean
  readonly failDetail?: boolean
}

function stub(plan: StubPlan = {}) {
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input)

      const fail = () =>
        new Response(
          JSON.stringify({ error: { code: 'INTERNAL', message: 'boom', correlation_id: 'cid-7' } }),
          { status: 500, headers: { 'content-type': 'application/json' } },
        )

      const ok = (data: unknown, page?: unknown) =>
        new Response(JSON.stringify({ data, meta: META, ...(page ? { page } : {}) }), {
          status: 200,
          headers: { 'content-type': 'application/json' },
        })

      if (url.includes('/evidence')) {
        return plan.failEvidence ? fail() : ok(plan.evidence ?? EVIDENCE)
      }
      if (url.includes('/transitions')) {
        return plan.failTransitions
          ? fail()
          : ok(plan.transitions ?? TRANSITIONS, { count: 2, has_more: false })
      }
      if (url.includes('/rankings/weights')) {
        return plan.failWeights ? fail() : ok(WEIGHTS)
      }

      return plan.failDetail ? fail() : ok(plan.detail ?? DETAIL)
    }),
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

describe('SignalScreen', () => {
  it('shows the confidence with its breakdown, per §15.4', async () => {
    render(<SignalScreen signalId="sig-1" />)

    const confidence = await screen.findByTestId('signal-confidence')

    expect(confidence.textContent).toContain('Confidence 75')
    expect(screen.getByTestId('factor-F3').textContent).toContain('80')
    // All six factors, not a sample.
    for (const key of ['F1', 'F2', 'F3', 'F4', 'F5', 'F6']) {
      expect(screen.getByTestId(`factor-${key}`)).toBeDefined()
    }
  })

  it('labels a factor with the server’s name and weight, never an invented one', async () => {
    render(<SignalScreen signalId="sig-1" />)

    const f3 = await screen.findByTestId('factor-F3')

    expect(f3.textContent).toContain('Liquidity context')
    expect(f3.textContent).toContain('20%')
    // F1 has no weights row in this fixture: bare key, no made-up name.
    expect(screen.getByTestId('factor-F1').textContent).not.toContain('—undefined')
  })

  it('does not show the number bare when the breakdown failed', async () => {
    // §15.4 forbids a bare number, and an error is not an exemption. The
    // tempting fallback — "show 75, hide the table" — is exactly the rendering
    // the sentence exists to prevent.
    stub({ failEvidence: true })

    render(<SignalScreen signalId="sig-1" />)

    const error = await screen.findByTestId('signal-evidence-error')

    expect(error.textContent).toContain('cid-7')
    expect(screen.queryByTestId('signal-confidence')).toBeNull()
  })

  it('puts the verdict before the argument', async () => {
    render(<SignalScreen signalId="sig-1" />)

    const outcome = await screen.findByTestId('signal-outcome')

    expect(outcome.textContent).toContain('SUCCESS after 9 candles')
    expect(outcome.textContent).toContain('9.159')
  })

  it('renders no outcome banner for a signal still running', async () => {
    const unresolved: Record<string, unknown> = { ...DETAIL, lifecycle_state: 'ACTIVE' }

    delete unresolved['outcome']

    stub({ detail: unresolved })

    render(<SignalScreen signalId="sig-1" />)

    await screen.findByTestId('signal-screen')

    expect(screen.queryByTestId('signal-outcome')).toBeNull()
  })

  it('shows the sealed R multiple rather than recomputing one', async () => {
    render(<SignalScreen signalId="sig-1" />)

    expect((await screen.findByTestId('signal-r-multiple')).textContent).toBe('5.112')
  })

  it('marks a stress test in words', async () => {
    // §12.4: a wick through invalidation records stress_test and does not fail
    // the signal — the single most misread event in the lifecycle, so the
    // timeline says it in a sentence rather than a flag.
    render(<SignalScreen signalId="sig-1" />)

    expect((await screen.findByTestId('signal-stress-test')).textContent).toContain(
      'close held',
    )
  })

  it('says the lifecycle failed rather than showing an empty history', async () => {
    // An empty history claims "nothing has happened to this signal". A 500
    // must not be allowed to assert that.
    stub({ failTransitions: true })

    render(<SignalScreen signalId="sig-1" />)

    expect(await screen.findByTestId('signal-transitions-error')).toBeDefined()
    expect(screen.queryByTestId('signal-lifecycle-empty')).toBeNull()
  })

  it('shows the seal and the server’s verdict on it', async () => {
    render(<SignalScreen signalId="sig-1" />)

    const hash = await screen.findByTestId('signal-hash')

    expect(hash.textContent).toContain('abcdef012345')
    expect(hash.textContent).toContain('verified')
  })

  it('shouts when the seal does not match', async () => {
    stub({ detail: { ...DETAIL, payload_hash_verified: false } })

    render(<SignalScreen signalId="sig-1" />)

    expect((await screen.findByTestId('signal-hash')).textContent).toContain('DOES NOT MATCH')
  })

  it('opens the chart on the entry zone', async () => {
    const opened: unknown[][] = []

    render(
      <SignalScreen
        signalId="sig-1"
        onOpenChart={(symbol, timeframe, object) => opened.push([symbol, timeframe, object])}
      />,
    )

    await screen.findByTestId('signal-confidence')

    fireEvent.click(screen.getByTestId('signal-open-chart'))

    // The zone id from the evidence row, prefixed the way the chart's deep
    // link expects — landing on the chart with nothing selected would waste
    // the one stable id the seal carries.
    expect(opened).toEqual([['ETHUSDT', 'H1', 'zone:zone-abc']])
  })

  it('shows the failure with its correlation id when the signal cannot be read', async () => {
    stub({ failDetail: true })

    render(<SignalScreen signalId="sig-1" />)

    expect((await screen.findByTestId('signal-error')).textContent).toContain('cid-7')
  })

  it('is axe-clean fully loaded', async () => {
    const { container } = render(<SignalScreen signalId="sig-1" />)

    await screen.findByTestId('signal-confidence')
    await screen.findByTestId('signal-lifecycle')

    const violations = await axeViolations(container)

    expect(violations, describeViolations(violations)).toEqual([])
  })

  it('reloads when pointed at a different signal', async () => {
    const { rerender } = render(<SignalScreen signalId="sig-1" />)

    await screen.findByTestId('signal-screen')

    stub({ detail: { ...DETAIL, signal_id: 'sig-2', symbol: 'BTCUSDT' } })

    rerender(<SignalScreen signalId="sig-2" />)

    await waitFor(() =>
      expect(screen.getByTestId('signal-screen').textContent).toContain('BTCUSDT'),
    )
  })
})
