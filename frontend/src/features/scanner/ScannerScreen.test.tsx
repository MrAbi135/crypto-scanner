import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { ScannerScreen } from './ScannerScreen'
import { setSession } from '@services/api/session'
import { axeViolations, describeViolations } from '@test/axe'

const ROW = {
  rank: 1,
  signal_id: 'sig-1',
  symbol: 'ETHUSDT',
  timeframe: 'H1',
  direction: 'UP',
  archetype: 'A3',
  grade: 'B',
  confidence: '75',
  display_rank: '61.5',
  age_candles: 4,
  entry: { proximal: '2483.52', distal: '2468.00' },
  invalidation: '2468.00',
  targets: {
    primary: { low: '2515.43', high: '2515.43', pool_id: 'p1', strength: '23.75' },
    secondary: null,
  },
  published_at: '2026-08-27T00:00:00+00:00',
  ttl_candles: 24,
  lifecycle_state: 'PUBLISHED',
  versions: { algo_version: 's8-confluence-v22', param_set_version: '2026.08.24.2' },
}

const META = {
  generated_at: '2026-08-27T01:00:00+00:00',
  freshness: { state: 'RECORDED', observed_at: '2026-08-27T00:00:00+00:00' },
}

function stubFeed(rows: unknown[], liveTotal = rows.length) {
  vi.stubGlobal(
    'fetch',
    vi.fn(async () =>
      Promise.resolve(
        new Response(
          JSON.stringify({
            data: rows,
            meta: META,
            page: { count: rows.length, has_more: false, live_total: liveTotal },
          }),
          { status: 200, headers: { 'content-type': 'application/json' } },
        ),
      ),
    ),
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

describe('ScannerScreen', () => {
  it('renders a signal with both confidence numbers', async () => {
    // §9.3 decays the display and leaves the recorded score alone. A reader
    // given only the decayed figure cannot tell a weakening signal from a weak
    // one, which is the distinction §15.4 exists to protect.
    stubFeed([ROW])

    render(<ScannerScreen />)

    const row = await screen.findByTestId('signal-sig-1')

    expect(row.textContent).toContain('61.5')
    expect(row.textContent).toContain('was 75')
  })

  it('does not say "was" when nothing has decayed', async () => {
    // A signal published on this candle has not weakened, and printing
    // "was 75" beside 75 would invent a movement that has not happened.
    stubFeed([{ ...ROW, display_rank: '75' }])

    render(<ScannerScreen />)

    const row = await screen.findByTestId('signal-sig-1')

    expect(row.textContent).not.toContain('was')
  })

  it('says the direction in words, not only in a glyph', async () => {
    // An arrow is unreadable to a screen reader and ambiguous to anyone who
    // has not learned which way this product points them.
    stubFeed([ROW])

    render(<ScannerScreen />)

    expect(await screen.findByText(/Long/)).toBeDefined()
  })

  it('shows prices exactly as the API sent them', async () => {
    // Canonical decimal strings (API §5). Reformatting one on the way to the
    // screen undoes the reason it is a string.
    stubFeed([ROW])

    render(<ScannerScreen />)

    const row = await screen.findByTestId('signal-sig-1')

    expect(row.textContent).toContain('2483.52')
    expect(row.textContent).toContain('2468.00')
  })

  it('renders §21.19’s quiet feed rather than a blank board', async () => {
    // "Absence as information." An empty board and a broken one look the same
    // otherwise, and this is the moment the Blueprint calls the scariest --
    // paying for silence.
    stubFeed([], 0)

    render(<ScannerScreen />)

    const quiet = await screen.findByTestId('quiet-feed')

    expect(quiet.textContent).toContain('floors held')
    expect(screen.queryByTestId('feed-board')).toBeNull()
  })

  it('offers exactly one primary action on the quiet feed', async () => {
    // §21.19: "Always exactly one primary action", and empties never
    // dead-end.
    stubFeed([], 0)

    render(<ScannerScreen />)

    const quiet = await screen.findByTestId('quiet-feed')

    // Scoped to the empty state. Counting every button on the screen made this
    // fail the moment filter chips arrived, which is the test being wrong
    // about what it was asserting rather than the screen being wrong.
    expect(within(quiet).getAllByRole('button')).toHaveLength(1)
  })

  it('shows the freshness the envelope carried', async () => {
    // §13. A board whose age the reader cannot see is a board they have to
    // trust.
    stubFeed([ROW])

    render(<ScannerScreen />)

    const chip = await screen.findByTestId('freshness')

    expect(chip.textContent).toContain('RECORDED')
  })

  it('shows the server’s failure with its correlation id', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () =>
        Promise.resolve(
          new Response(
            JSON.stringify({
              error: { code: 'DEGRADED_DEPENDENCY', message: 'no', correlation_id: 'cid-9' },
            }),
            { status: 503, headers: { 'content-type': 'application/json' } },
          ),
        ),
      ),
    )

    render(<ScannerScreen />)

    const alert = await screen.findByRole('alert')

    expect(alert.textContent).toContain('cid-9')
    // A failure is not a quiet market, and must not render as one.
    expect(screen.queryByTestId('quiet-feed')).toBeNull()
  })

  it('is axe-clean with rows', async () => {
    stubFeed([ROW])

    const { container } = render(<ScannerScreen />)

    await screen.findByTestId('feed-board')

    const violations = await axeViolations(container)

    expect(violations, describeViolations(violations)).toEqual([])
  })

  it('is axe-clean when quiet', async () => {
    stubFeed([], 0)

    const { container } = render(<ScannerScreen />)

    await screen.findByTestId('quiet-feed')

    const violations = await axeViolations(container)

    expect(violations, describeViolations(violations)).toEqual([])
  })
})

describe('QuietFeed distinguishes its two emptinesses', () => {
  it('blames the filter when the filter is what hid them', async () => {
    // §21.19 catalogs "empty history filter (state the filter, offer clear)"
    // separately from the quiet feed. Rendering one as the other tells a user
    // the market is silent when their own chip is.
    const { QuietFeed } = await import('./QuietFeed')

    render(
      <QuietFeed
        liveTotal={12}
        filtered
        onShowFloors={vi.fn()}
        onClearFilter={vi.fn()}
      />,
    )

    expect(screen.getByTestId('quiet-feed').textContent).toContain('12 signals are live')
    expect(screen.getByRole('button', { name: 'Clear the filter' })).toBeDefined()
  })

  it('does not blame a filter that is hiding nothing', async () => {
    // Filtered, but there is nothing live to hide. The market is the reason.
    const { QuietFeed } = await import('./QuietFeed')

    render(
      <QuietFeed liveTotal={0} filtered onShowFloors={vi.fn()} onClearFilter={vi.fn()} />,
    )

    await waitFor(() =>
      expect(screen.getByTestId('quiet-feed').textContent).toContain('floors held'),
    )
  })
})

describe('ScannerScreen filters', () => {
  function lastUrl(): string {
    const calls = (globalThis.fetch as unknown as { mock: { calls: unknown[][] } }).mock.calls

    return String(calls[calls.length - 1]?.[0])
  }

  it('sends the filter to the server rather than narrowing the rows here', async () => {
    // §9: a filter the server did not apply "is a lie the client believes".
    // Filtering client-side would also break `live_total`, which is only an
    // honest denominator because the server reports what it did not send.
    stubFeed([ROW])

    render(<ScannerScreen />)

    await screen.findByTestId('feed-board')

    fireEvent.click(screen.getByTestId('chip-grade-B'))

    await waitFor(() => expect(decodeURIComponent(lastUrl())).toContain('filter[grade]=B'))
  })

  it('uses the [in] operator only when there is more than one value', async () => {
    // A request should say what it means, and `[in]` with one member reads as
    // a list that happens to be short.
    stubFeed([ROW])

    render(<ScannerScreen />)

    await screen.findByTestId('feed-board')

    fireEvent.click(screen.getByTestId('chip-grade-B'))
    await waitFor(() => expect(decodeURIComponent(lastUrl())).toContain('filter[grade]=B'))

    fireEvent.click(screen.getByTestId('chip-grade-A'))
    await waitFor(() =>
      expect(decodeURIComponent(lastUrl())).toContain('filter[grade][in]=B,A'),
    )
  })

  it('announces which chips are on', async () => {
    // `aria-pressed`, because a chip whose state is carried only by a border
    // is invisible to a screen reader -- and the board cannot be read at all
    // without knowing what narrowed it.
    stubFeed([ROW])

    render(<ScannerScreen />)

    await screen.findByTestId('feed-board')

    const chip = screen.getByTestId('chip-direction-UP')

    expect(chip.getAttribute('aria-pressed')).toBe('false')

    fireEvent.click(chip)

    await waitFor(() => expect(chip.getAttribute('aria-pressed')).toBe('true'))
  })

  it('names the filter when it is the filter that emptied the board', async () => {
    // §21.19: "state the filter, offer clear". The difference between a user
    // knowing what to undo and guessing.
    stubFeed([], 12)

    render(<ScannerScreen />)

    await screen.findByTestId('quiet-feed')

    fireEvent.click(screen.getByTestId('chip-grade-S'))

    await waitFor(() => {
      const quiet = screen.getByTestId('quiet-feed')

      expect(quiet.textContent).toContain('12 signals are live')
      expect(quiet.textContent).toContain('grade S')
    })

    expect(screen.getByRole('button', { name: 'Clear the filter' })).toBeDefined()
  })

  it('clears back to the unfiltered request', async () => {
    stubFeed([ROW])

    render(<ScannerScreen />)

    await screen.findByTestId('feed-board')

    fireEvent.click(screen.getByTestId('chip-grade-B'))
    await waitFor(() => expect(decodeURIComponent(lastUrl())).toContain('filter[grade]'))

    fireEvent.click(screen.getByRole('button', { name: 'Clear all' }))

    await waitFor(() => expect(decodeURIComponent(lastUrl())).not.toContain('filter['))
  })
})
