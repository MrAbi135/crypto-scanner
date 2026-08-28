import { describe, expect, it } from 'vitest'

import { search, symbolCommands, type Command } from './commands'

const SCREENS: Command[] = [
  { id: 'screen:feed', group: 'Screens', label: 'Live feed', keywords: 'feed', detail: 'Go' },
  { id: 'screen:chart', group: 'Screens', label: 'Chart', keywords: 'chart', detail: 'Go' },
]

const SYMBOLS = symbolCommands([
  {
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
  },
  {
    symbol: 'ETHUSDT',
    base_asset: 'ETH',
    quote_asset: 'USDT',
    status: 'QUARANTINE',
    tier: 'INELIGIBLE',
    candidate_tier: null,
    consecutive_passes: 0,
    consecutive_failures: 0,
    observation_days: 3,
    assessment: 'collecting',
    first_seen_at: '2026-08-25T00:00:00+00:00',
  },
])

const ALL = [...SCREENS, ...SYMBOLS]

// Hand-built rather than drawn from the fixtures above: the two middle ranks
// only compete when one row matches in its label and another only in its
// keywords, and no pair of real screens and symbols happens to do that. The
// ranking is still real code with a real effect on which row Enter opens.
function command(label: string, keywords: string): Command {
  return { id: `x:${label}`, group: 'Test', label, keywords, detail: '' }
}

describe('palette ranking', () => {
  it('puts a label prefix above a label that merely contains the query', () => {
    const rows = [command('abtc', ''), command('btcx', '')]

    expect(search(rows, 'btc').map((c) => c.label)).toEqual(['btcx', 'abtc'])
  })

  it('puts a label match above a match only its hidden keywords have', () => {
    // The label is what the reader is looking at. A row that matches on words
    // they cannot see should not jump above the row they can read.
    const rows = [command('zeta', 'btc'), command('abtc', '')]

    expect(search(rows, 'btc').map((c) => c.label)).toEqual(['abtc', 'zeta'])
  })
})

describe('palette search', () => {
  it('puts a prefix match above a match in the middle', () => {
    // The reader types the first letters of the thing they want. If "ch" ranks
    // an unrelated row that merely contains those letters first, Enter opens
    // the wrong thing and the palette stops being usable without looking.
    expect(search(ALL, 'ch')[0]?.label).toBe('Chart')
  })

  it('finds a symbol by its base asset', () => {
    // Nobody thinks of it as ETHUSDT. The pair is what the API calls it.
    expect(search(ALL, 'eth').map((c) => c.label)).toEqual(['ETHUSDT'])
  })

  it('matches letters in order without requiring them adjacent', () => {
    expect(search(ALL, 'btct').map((c) => c.label)).toEqual(['BTCUSDT'])
  })

  it('refuses letters that are not there in order', () => {
    // The whole point of a subsequence match rather than "contains any letter":
    // a query that is not a prefix of anything must return nothing rather than
    // everything.
    expect(search(ALL, 'zzz')).toEqual([])
  })

  it('returns everything, in the order given, for an empty query', () => {
    // §21.12 wants recents on an empty input; there is no recents store, so the
    // honest default is the full list in a stable order rather than an
    // arbitrary slice pretending to be recents.
    expect(search(ALL, '  ').map((c) => c.label)).toEqual(ALL.map((c) => c.label))
  })

  it('does not reshuffle two equally good matches between calls', () => {
    // A list navigated by muscle memory cannot reorder under identical input.
    const once = search(ALL, 'usdt').map((c) => c.label)
    const twice = search(ALL, 'usdt').map((c) => c.label)

    expect(once).toEqual(twice)
    expect(once).toEqual(['BTCUSDT', 'ETHUSDT'])
  })

  it('carries the status that explains a missing signal', () => {
    // A QUARANTINE symbol is not being scanned, so "why is there no signal for
    // ETHUSDT" is answered in the result row rather than on another screen.
    expect(search(ALL, 'eth')[0]?.detail).toContain('QUARANTINE')
  })
})
