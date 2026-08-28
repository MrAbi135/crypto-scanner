import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { App } from './App'
import { setSession } from '@services/api/session'

const META = {
  generated_at: '2026-08-27T01:00:00+00:00',
  freshness: { state: 'RECORDED' },
}

beforeEach(() => {
  setSession({
    accessToken: 't',
    userId: 'u1',
    tenantId: 't1',
    expiresAt: Date.now() + 900_000,
  })

  // Every screen empty. This file is about reaching them, not their contents.
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input)
      const data = url.includes('/rankings/weights')
        ? { param_set_version: 'v', factors: [], grades: [], below_lowest_floor: 'not published' }
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
})

afterEach(() => {
  vi.unstubAllGlobals()
  setSession(null)
})

describe('App shell', () => {
  it('mounts and shows the product name', () => {
    render(<App />)

    expect(
      screen.getByRole('heading', { name: /Institutional AI Crypto Scanner/i }),
    ).toBeDefined()
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

    fireEvent.click(screen.getByRole('tab', { name: 'Live feed' }))
    expect(await screen.findByRole('heading', { name: 'Live feed' })).toBeDefined()
  })

  it('lands "See the floors" on the floors', async () => {
    // §21.19: an empty never dead-ends. The action existed and went nowhere
    // until there was somewhere to go.
    render(<App />)

    fireEvent.click(await screen.findByRole('button', { name: 'See the floors' }))

    expect(await screen.findByRole('heading', { name: 'Rankings' })).toBeDefined()
  })

  it('says which view is selected, in both channels', async () => {
    render(<App />)

    const feed = screen.getByRole('tab', { name: 'Live feed' })

    expect(feed.getAttribute('aria-selected')).toBe('true')
    expect(feed.getAttribute('class')).toContain('app__view--on')
  })
})
