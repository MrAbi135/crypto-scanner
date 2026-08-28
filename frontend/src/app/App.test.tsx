import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { App } from './App'
import { setSession } from '@services/api/session'

const META = {
  generated_at: '2026-08-27T01:00:00+00:00',
  freshness: { state: 'RECORDED' },
}

// One live row, so the feed has something to click through from. Its shape is
// §18.4's `summary` projection; only the fields this file reaches for matter.
const FEED_ROW = {
  rank: 1,
  signal_id: 'sig-1',
  symbol: 'ETHUSDT',
  timeframe: 'H1',
  direction: 'UP',
  archetype: 'A3',
  grade: 'B',
  confidence: '75',
  display_rank: '75',
  age_candles: 2,
  entry: { proximal: '1', distal: '2' },
  invalidation: '0.5',
  targets: { primary: null, secondary: null },
  published_at: '2026-08-27T00:00:00+00:00',
  ttl_candles: 24,
  lifecycle_state: 'ACTIVE',
  versions: { algo_version: 'a', param_set_version: 'p' },
}

beforeEach(() => {
  // The shell reads the address, and `pushState` in one test is still there in
  // the next -- jsdom keeps one Location per file. Without this reset the
  // second test in the file starts wherever the first navigated to.
  window.history.replaceState(null, '', '/')

  setSession({
    accessToken: 't',
    userId: 'u1',
    tenantId: 't1',
    expiresAt: Date.now() + 900_000,
  })

  stubFetch([FEED_ROW])
})

/** Every screen empty but the feed, which serves `rows`. */
function stubFetch(rows: readonly unknown[]) {
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input)
      const data = url.includes('/scanner/feed')
        ? rows
        : url.includes('/dashboard/overview')
          ? { top_signals: [], live_total: 0, recent_sweeps: [], not_measured: [] }
        : url.includes('/signals/statistics')
          ? []
          : url.includes('/signals/history')
            ? []
        : url.includes('/evidence')
          ? {
              signal_id: 'sig-1',
              symbol: 'ETHUSDT',
              timeframe: 'H1',
              evidence_ids: [],
              entry_zone_id: 'zone-9',
              confidence: { final: '75', grade: 'B', factors: { F1: '70' } },
              reason: null,
              htf_chain: {},
              risk: {},
            }
          : url.includes('/transitions')
            ? []
            : /\/signals\/[^/]+$/.test(url.split('?')[0] ?? '')
              ? FEED_ROW
              : url.includes('/dashboard/status')
          ? {
              feeds: [],
              behind_count: 0,
              degraded: [],
              degraded_count: 0,
              not_measured: ['storm_mode — lives in the engine process'],
            }
          : url.includes('/rankings/weights')
            ? {
                param_set_version: 'v',
                factors: [],
                grades: [],
                below_lowest_floor: 'not published',
              }
            : url.includes('/liquidity')
              ? { pools: [], sweeps: [] }
              : []

      return Promise.resolve(
        new Response(
          JSON.stringify({ data, meta: META, page: { count: 0, has_more: false, live_total: 0 } }),
          { status: 200, headers: { 'content-type': 'application/json' } },
        ),
      )
    }),
  )
}

afterEach(() => {
  vi.unstubAllGlobals()
  setSession(null)
})

describe('App shell', () => {
  it('mounts and shows the product name', () => {
    render(<App />)

    expect(screen.getByRole('heading', { name: /Institutional AI Crypto Scanner/i })).toBeDefined()
  })

  it('opens on the feed, because that is the core read', async () => {
    render(<App />)

    expect(await screen.findByRole('heading', { name: 'Live feed' })).toBeDefined()
  })

  it('reaches every screen', async () => {
    // S14 added two screens nothing could open. A screen nobody can reach is
    // indistinguishable from one that was never written -- which is how the
    // S13a chart came to be built, tested and merged while being invisible on
    // the host for a fortnight.
    render(<App />)

    fireEvent.click(screen.getByRole('tab', { name: 'Rankings' }))
    expect(await screen.findByRole('heading', { name: 'Rankings' })).toBeDefined()

    fireEvent.click(screen.getByRole('tab', { name: 'Chart' }))
    await waitFor(() => expect(screen.getByLabelText('Symbol')).toBeDefined())

    fireEvent.click(screen.getByRole('tab', { name: 'Track record' }))
    expect(await screen.findByRole('heading', { name: 'Track record' })).toBeDefined()

    fireEvent.click(screen.getByRole('tab', { name: 'Dashboard' }))
    expect(await screen.findByRole('heading', { name: 'Dashboard' })).toBeDefined()

    fireEvent.click(screen.getByRole('tab', { name: 'Live feed' }))
    expect(await screen.findByRole('heading', { name: 'Live feed' })).toBeDefined()
  })

  it('lands "See the floors" on the floors', async () => {
    // §21.19: an empty never dead-ends. The action existed and went nowhere
    // until there was somewhere to go.
    //
    // Re-stubbed empty: the quiet state is the subject here, and the shared
    // fixture serves a live row so the rest of the file has something to click.
    stubFetch([])

    render(<App />)

    fireEvent.click(await screen.findByRole('button', { name: 'See the floors' }))

    expect(await screen.findByRole('heading', { name: 'Rankings' })).toBeDefined()
  })

  it('keeps the status strip outside the switcher', async () => {
    // Whether the platform is covered is not a property of the panel you
    // happen to be looking at. A board of stale rows looks identical to a
    // board of fresh ones, on every tab.
    render(<App />)

    expect(await screen.findByTestId('status-not-measured')).toBeDefined()

    fireEvent.click(screen.getByRole('tab', { name: 'Rankings' }))

    expect(screen.getByTestId('status-not-measured')).toBeDefined()
  })

  it('opens a signal’s own context on the chart', async () => {
    // The transcription step this removes: a reader who saw ETHUSDT H1 in the
    // feed had to switch tabs and retype both, in a product whose whole claim
    // is that the evidence is one click away.
    render(<App />)

    fireEvent.click(await screen.findByTestId('open-sig-1'))

    await waitFor(() => expect(screen.getByLabelText('Symbol')).toBeDefined())

    expect((screen.getByLabelText('Symbol') as HTMLInputElement).value).toBe('ETHUSDT')
    expect((screen.getByLabelText('Timeframe') as HTMLSelectElement).value).toBe('H1')
  })

  it('re-opens the same row after the reader has wandered off it', async () => {
    // Open a row, type something else into the chart's own box, come back and
    // click the same row. It works because the switcher unmounts the panel it
    // is not showing, so every arrival re-reads `openOn` -- and this test is
    // here to fail if the shell ever keeps the chart mounted, which would
    // leave the second click doing nothing at all.
    render(<App />)

    fireEvent.click(await screen.findByTestId('open-sig-1'))

    await waitFor(() => expect(screen.getByLabelText('Symbol')).toBeDefined())

    fireEvent.change(screen.getByLabelText('Symbol'), { target: { value: 'SOLUSDT' } })
    expect((screen.getByLabelText('Symbol') as HTMLInputElement).value).toBe('SOLUSDT')

    fireEvent.click(screen.getByRole('tab', { name: 'Live feed' }))
    fireEvent.click(await screen.findByTestId('open-sig-1'))

    await waitFor(() =>
      expect((screen.getByLabelText('Symbol') as HTMLInputElement).value).toBe('ETHUSDT'),
    )
  })

  it('puts the screen in the address bar', async () => {
    render(<App />)

    fireEvent.click(screen.getByRole('tab', { name: 'Rankings' }))

    await waitFor(() => expect(window.location.pathname).toBe('/rankings'))
  })

  it('opens on the screen the address names', async () => {
    // The half that makes a link worth sending. Writing to the address bar
    // without reading from it is a URL that only ever describes where somebody
    // else already was.
    window.history.replaceState(null, '', '/universe')

    render(<App />)

    expect(await screen.findByRole('heading', { name: 'Universe' })).toBeDefined()
  })

  it('opens the chart on the context the address names', async () => {
    window.history.replaceState(null, '', '/chart/ETHUSDT/H4')

    render(<App />)

    await waitFor(() => expect(screen.getByLabelText('Symbol')).toBeDefined())

    expect((screen.getByLabelText('Symbol') as HTMLInputElement).value).toBe('ETHUSDT')
    expect((screen.getByLabelText('Timeframe') as HTMLSelectElement).value).toBe('H4')
  })

  it('follows the chart’s own context into the address bar', async () => {
    render(<App />)

    fireEvent.click(await screen.findByTestId('open-sig-1'))

    await waitFor(() => expect(window.location.pathname).toBe('/chart/ETHUSDT/H1'))

    fireEvent.change(screen.getByLabelText('Symbol'), { target: { value: 'SOLUSDT' } })

    await waitFor(() => expect(window.location.pathname).toBe('/chart/SOLUSDT/H1'))
  })

  it('goes back', async () => {
    // The whole reason to write to the address bar. A `pushState` with no
    // `popstate` listener fills the history stack with entries that do nothing
    // when the reader returns to them -- worse than no history, because the
    // button looks like it works.
    render(<App />)

    fireEvent.click(screen.getByRole('tab', { name: 'Rankings' }))

    await waitFor(() => expect(window.location.pathname).toBe('/rankings'))

    window.history.back()

    // jsdom dispatches popstate asynchronously.
    await waitFor(() => expect(screen.getByRole('heading', { name: 'Live feed' })).toBeDefined())
  })

  it('takes the chart back to the context it came from', async () => {
    // The case the chart's own state would break: it seeds from `openOn` and
    // then owns the value, so a history entry arriving from outside has to
    // re-seed it or the address and the screen part company.
    window.history.replaceState(null, '', '/chart/ETHUSDT/H4')

    render(<App />)

    await waitFor(() => expect(screen.getByLabelText('Symbol')).toBeDefined())

    fireEvent.change(screen.getByLabelText('Symbol'), { target: { value: 'SOLUSDT' } })

    await waitFor(() => expect(window.location.pathname).toBe('/chart/SOLUSDT/H4'))

    window.history.back()

    await waitFor(() =>
      expect((screen.getByLabelText('Symbol') as HTMLInputElement).value).toBe('ETHUSDT'),
    )
  })

  it('opens a signal’s detail from its feed row, and the address follows', async () => {
    // J2's second joint: feed row → the conviction surface. Before this the
    // factor breakdown existed on the server and nothing could reach it.
    render(<App />)

    fireEvent.click(await screen.findByTestId('detail-sig-1'))

    await waitFor(() => expect(window.location.pathname).toBe('/signal/sig-1'))
    expect(await screen.findByTestId('signal-screen')).toBeDefined()
  })

  it('opens the signal the address names', async () => {
    window.history.replaceState(null, '', '/signal/sig-1')

    render(<App />)

    expect(await screen.findByTestId('signal-screen')).toBeDefined()
  })

  it('walks J2: feed row → detail → chart, with the zone in the address', async () => {
    // S16's DoD names this journey. The last joint — detail to chart — must
    // carry the entry zone into the URL, or the reader lands on the right
    // chart with nothing selected and the deep link the seal paid for is
    // wasted.
    render(<App />)

    fireEvent.click(await screen.findByTestId('detail-sig-1'))

    fireEvent.click(await screen.findByTestId('signal-open-chart'))

    await waitFor(() =>
      expect(window.location.pathname + window.location.search).toBe(
        '/chart/ETHUSDT/H1?object=zone%3Azone-9',
      ),
    )
    expect((screen.getByLabelText('Symbol') as HTMLInputElement).value).toBe('ETHUSDT')
  })

  it('says which view is selected, in both channels', async () => {
    render(<App />)

    const feed = screen.getByRole('tab', { name: 'Live feed' })

    expect(feed.getAttribute('aria-selected')).toBe('true')
    expect(feed.getAttribute('class')).toContain('app__view--on')
  })
})
