import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { CommandPalette } from './CommandPalette'
import { setSession } from '@services/api/session'
import { axeViolations, describeViolations } from '@test/axe'

const META = { generated_at: '2026-08-28T12:00:00+00:00', freshness: { state: 'RECORDED' } }

const VIEWS = [
  { id: 'feed', label: 'Live feed' },
  { id: 'chart', label: 'Chart' },
] as const

const SYMBOL = {
  symbol: 'BTCUSDT',
  base_asset: 'BTC',
  quote_asset: 'USDT',
  status: 'ACTIVE',
  tier: 'TIER_1',
  candidate_tier: null,
  consecutive_passes: 7,
  consecutive_failures: 0,
  observation_days: 30,
  assessment: 'evaluating',
  first_seen_at: '2026-07-01T00:00:00+00:00',
}

function stub(rows: unknown[] = [SYMBOL], status = 200) {
  vi.stubGlobal(
    'fetch',
    vi.fn(async () =>
      Promise.resolve(
        new Response(
          JSON.stringify(
            status === 200
              ? { data: rows, meta: META, page: { count: rows.length, has_more: false } }
              : { error: { code: 'INTERNAL', message: 'no', correlation_id: 'cid' } },
          ),
          { status, headers: { 'content-type': 'application/json' } },
        ),
      ),
    ),
  )
}

function mount(onScreen = vi.fn(), onSymbol = vi.fn()) {
  const rendered = render(
    <CommandPalette views={VIEWS} onScreen={onScreen} onSymbol={onSymbol} />,
  )

  return { ...rendered, onScreen, onSymbol }
}

function openIt() {
  fireEvent.keyDown(window, { key: 'k', ctrlKey: true })
}

beforeEach(() => {
  setSession({ accessToken: 't', userId: 'u', tenantId: 't', expiresAt: Date.now() + 9e5 })
  stub()
})

afterEach(() => {
  vi.unstubAllGlobals()
  setSession(null)
})

describe('CommandPalette', () => {
  it('renders nothing at all until the shortcut is pressed', () => {
    // Chrome on every screen. If it mounted its markup permanently it would sit
    // in the tab order of every page for a feature most sessions never use.
    mount()

    expect(screen.queryByTestId('palette')).toBeNull()
  })

  it('opens on ctrl+k and closes on escape', async () => {
    mount()

    openIt()

    const input = await screen.findByLabelText('Search symbols and screens')

    fireEvent.keyDown(input, { key: 'Escape' })

    expect(screen.queryByTestId('palette')).toBeNull()
  })

  it('does not ask for symbols until it is opened', async () => {
    // A universe request on every page load, to populate a list most sessions
    // never open, is a cost with no reader attached.
    mount()

    expect(globalThis.fetch).not.toHaveBeenCalled()

    openIt()

    await waitFor(() => expect(globalThis.fetch).toHaveBeenCalled())
  })

  it('reaches a screen with arrows and Enter alone', async () => {
    // Constitution §23.4 is a keyboard promise, so the whole path is exercised
    // without a single click.
    const { onScreen } = mount()

    openIt()

    const input = await screen.findByLabelText('Search symbols and screens')

    fireEvent.change(input, { target: { value: 'chart' } })
    fireEvent.keyDown(input, { key: 'Enter' })

    expect(onScreen).toHaveBeenCalledWith('chart')
  })

  it('reaches a symbol the same way', async () => {
    const { onSymbol } = mount()

    openIt()

    const input = await screen.findByLabelText('Search symbols and screens')

    await screen.findByTestId('palette-symbol:BTCUSDT')

    fireEvent.change(input, { target: { value: 'btc' } })
    fireEvent.keyDown(input, { key: 'Enter' })

    expect(onSymbol).toHaveBeenCalledWith('BTCUSDT')
  })

  it('moves the active option with the arrow keys', async () => {
    const { onScreen } = mount()

    openIt()

    const input = await screen.findByLabelText('Search symbols and screens')

    // Two screens are listed first; one press down lands on the second.
    fireEvent.keyDown(input, { key: 'ArrowDown' })
    fireEvent.keyDown(input, { key: 'Enter' })

    expect(onScreen).toHaveBeenCalledWith('chart')
  })

  it('wraps at the end rather than sticking on the last row', async () => {
    // Three rows on an empty query, so three presses are back at the top. A
    // clamp instead of a wrap looks identical for one press and then never
    // moves again -- which was the first version, and the first test written
    // for it could not tell the two apart, because it only ever pressed once
    // against a single-row list.
    const { onScreen } = mount()

    openIt()

    const input = await screen.findByLabelText('Search symbols and screens')

    // Live feed, Chart, BTCUSDT -- awaited, so the count is not a race.
    await screen.findByTestId('palette-symbol:BTCUSDT')

    fireEvent.keyDown(input, { key: 'ArrowDown' })
    fireEvent.keyDown(input, { key: 'ArrowDown' })
    fireEvent.keyDown(input, { key: 'ArrowDown' })
    fireEvent.keyDown(input, { key: 'Enter' })

    expect(onScreen).toHaveBeenCalledWith('feed')
  })

  it('wraps backwards from the first row to the last', async () => {
    const { onSymbol } = mount()

    openIt()

    const input = await screen.findByLabelText('Search symbols and screens')

    await screen.findByTestId('palette-symbol:BTCUSDT')

    fireEvent.keyDown(input, { key: 'ArrowUp' })
    fireEvent.keyDown(input, { key: 'Enter' })

    expect(onSymbol).toHaveBeenCalledWith('BTCUSDT')
  })

  it('keeps the keyboard on the text field while navigating', async () => {
    // The active option is announced through aria-activedescendant precisely so
    // focus can stay put: move it into the list and typing stops working
    // halfway through a query.
    mount()

    openIt()

    const input = await screen.findByLabelText('Search symbols and screens')

    fireEvent.keyDown(input, { key: 'ArrowDown' })

    expect(document.activeElement).toBe(input)
    expect(input.getAttribute('aria-activedescendant')).toBe('palette-screen:chart')
  })

  it('says a symbol it cannot find is a symbol not in the universe', async () => {
    // "No results" is a shrug. The reader's real question is why, and for a
    // symbol there is exactly one honest answer.
    mount()

    openIt()

    fireEvent.change(await screen.findByLabelText('Search symbols and screens'), {
      target: { value: 'zzzzz' },
    })

    expect(screen.getByTestId('palette-empty').textContent).toContain('universe')
  })

  it('says the symbol list failed rather than showing an empty one', async () => {
    // A palette that quietly lists no symbols is indistinguishable from a
    // platform that has none.
    stub([], 500)

    mount()

    openIt()

    expect(await screen.findByTestId('palette-symbol-error')).toBeDefined()
  })

  it('names the groups it cannot search yet', async () => {
    // §21.12 lists five groups and two are built. Silently reaching two of five
    // teaches a reader that the missing three do not exist.
    mount()

    openIt()

    const note = await screen.findByTestId('palette-not-reached')

    expect(note.textContent).toContain('Signals by id')
    expect(note.textContent).toContain('amendment')
  })

  it('is axe-clean with results on screen', async () => {
    const { container } = mount()

    openIt()

    await screen.findByTestId('palette-symbol:BTCUSDT')

    const violations = await axeViolations(container)

    expect(violations, describeViolations(violations)).toEqual([])
  })
})
