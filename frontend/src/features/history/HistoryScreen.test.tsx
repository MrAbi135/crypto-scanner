import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { HistoryScreen } from './HistoryScreen'
import { setSession } from '@services/api/session'
import { axeViolations, describeViolations } from '@test/axe'

const META = { generated_at: '2026-08-28T12:00:00+00:00', freshness: { state: 'RECORDED' } }

const RESOLVED = {
  signal_id: 'sig-1',
  symbol: 'ETHUSDT',
  timeframe: 'H1',
  direction: 'UP',
  archetype: 'A3',
  grade: 'B',
  confidence: '75',
  published_at: '2026-08-26T23:00:00+00:00',
  versions: { algo_version: 's8-confluence-v22', param_set_version: '2026.08.24.2' },
  outcome: {
    outcome: 'SUCCESS',
    resolved_at: '2026-08-27T08:00:00+00:00',
    elapsed_candles: 9,
    mfe_r: '9.159',
    mae_r: '0',
    excluded_from_stats: false,
  },
}

const LIVE = {
  ...RESOLVED,
  signal_id: 'sig-2',
  outcome: undefined,
}

const GROUP = {
  group_by: 'archetype',
  key: 'A3',
  algo_version: 's8-confluence-v22',
  counts: { resolved: 1, success: 1, failed: 0, expired: 0, invalidated_early: 0 },
  hit_rate: {
    rated: 1,
    rate_pct: '100',
    confidence_interval: { level: '95%', low_pct: '20.7', high_pct: '100' },
    sufficient_for_inference: false,
    label: 'n=1 — insufficient for inference',
  },
}

function stub({
  rows = [RESOLVED, LIVE] as unknown[],
  groups = [GROUP] as unknown[],
  failStats = false,
  failHistory = false,
} = {}) {
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input)

      const fail = () =>
        new Response(
          JSON.stringify({ error: { code: 'INTERNAL', message: 'boom', correlation_id: 'cid-3' } }),
          { status: 500, headers: { 'content-type': 'application/json' } },
        )

      if (url.includes('/statistics')) {
        return failStats
          ? fail()
          : new Response(
              JSON.stringify({ data: groups, meta: META, page: { count: groups.length } }),
              { status: 200, headers: { 'content-type': 'application/json' } },
            )
      }

      return failHistory
        ? fail()
        : new Response(
            JSON.stringify({ data: rows, meta: META, page: { count: rows.length } }),
            { status: 200, headers: { 'content-type': 'application/json' } },
          )
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

describe('HistoryScreen', () => {
  it('shows the archive with each row’s outcome', async () => {
    render(<HistoryScreen />)

    const row = await screen.findByTestId('archived-sig-1')

    expect(row.textContent).toContain('SUCCESS')
    expect(row.textContent).toContain('9.159R')
  })

  it('shows a live row as live rather than inventing an outcome', async () => {
    render(<HistoryScreen />)

    expect((await screen.findByTestId('outcome-of-sig-2')).textContent).toBe('live')
  })

  it('renders the small-sample label verbatim from the payload', async () => {
    // PRD FC-10.1 puts the honesty in the stat primitive. The label is the
    // server's phrasing — rendering our own would let the two drift.
    render(<HistoryScreen />)

    expect((await screen.findByTestId('small-sample')).textContent).toBe(
      'n=1 — insufficient for inference',
    )
  })

  it('renders an unrated group as unstated, never as 0%', async () => {
    // Zero is a claim from no evidence, and the server sends null for exactly
    // that reason. A `?? '0'` here would overrule it.
    stub({
      groups: [
        {
          ...GROUP,
          hit_rate: { ...GROUP.hit_rate, rated: 0, rate_pct: null, confidence_interval: null },
        },
      ],
    })

    render(<HistoryScreen />)

    expect(await screen.findByTestId('rate-unstated')).toBeDefined()
    expect(screen.getByTestId('stats-A3').textContent).not.toContain('%')
  })

  it('carries the version beside every group', async () => {
    // §18.8: version-segmented always. A hit rate averaged over two algo
    // versions is the average of two different scanners.
    render(<HistoryScreen />)

    expect((await screen.findByTestId('stats-A3')).textContent).toContain('s8-confluence-v22')
  })

  it('marks a row the statistics are not counting', async () => {
    stub({
      rows: [
        {
          ...RESOLVED,
          outcome: { ...RESOLVED.outcome, outcome: 'EXPIRED_DELISTED', excluded_from_stats: true },
        },
      ],
    })

    render(<HistoryScreen />)

    expect(await screen.findByTestId('excluded-sig-1')).toBeDefined()
  })

  it('filters on the server', async () => {
    render(<HistoryScreen />)

    await screen.findByTestId('history-table')

    fireEvent.click(screen.getByTestId('outcome-FAILED'))

    await waitFor(() => {
      const calls = (globalThis.fetch as unknown as { mock: { calls: unknown[][] } }).mock.calls

      expect(
        decodeURIComponent(String(calls[calls.length - 1]?.[0])),
      ).toContain('filter[outcome]=FAILED')
    })
  })

  it('re-asks for statistics when the window changes', async () => {
    render(<HistoryScreen />)

    await screen.findByTestId('stats-groups')

    fireEvent.click(screen.getByTestId('window-30d'))

    await waitFor(() => {
      const calls = (globalThis.fetch as unknown as { mock: { calls: unknown[][] } }).mock.calls
      const urls = calls.map((call) => String(call[0]))

      expect(urls.some((url) => url.includes('window=30d'))).toBe(true)
    })
  })

  it('says the statistics failed rather than showing none', async () => {
    // "Nothing resolved yet" is a claim about the record. A 500 must not make
    // it.
    stub({ failStats: true })

    render(<HistoryScreen />)

    expect((await screen.findByTestId('stats-error')).textContent).toContain('cid-3')
    expect(screen.queryByTestId('stats-empty')).toBeNull()
  })

  it('says an empty archive is the record, not a failure', async () => {
    stub({ rows: [] })

    render(<HistoryScreen />)

    expect((await screen.findByTestId('history-empty')).textContent).toContain('the record')
  })

  it('opens each row’s own signal, not the first one', async () => {
    // Asserted on the second row deliberately: with only one row in the
    // fixture, a handler hard-wired to the first id passes every test that
    // exists. That mutation survived until this clicked sig-2.
    const opened: string[] = []

    render(<HistoryScreen onOpenSignal={(id) => opened.push(id)} />)

    fireEvent.click(await screen.findByTestId('open-archived-sig-2'))
    fireEvent.click(screen.getByTestId('open-archived-sig-1'))

    expect(opened).toEqual(['sig-2', 'sig-1'])
  })

  it('is axe-clean', async () => {
    const { container } = render(<HistoryScreen />)

    await screen.findByTestId('history-table')
    await screen.findByTestId('stats-groups')

    const violations = await axeViolations(container)

    expect(violations, describeViolations(violations)).toEqual([])
  })
})
