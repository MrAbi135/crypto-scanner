// What the palette can reach, and -- said out loud -- what it cannot.
//
// Blueprint §21.12 lists five groups: Symbols, Signals, Screens & Actions,
// Concepts, Settings. Two are built here. The other three each need something
// that does not exist yet, and the palette *says so on screen* rather than
// quietly omitting them:
//
//   * Signals by id need the consolidated search resource, which is an API
//     Spec amendment (Roadmap S15 names it explicitly: "amendment before
//     implementation, never silent endpoint invention"). That is the
//     developer's step, not one to route around.
//   * Concepts need the SLS glossary as data. It is 17,000 lines of prose in
//     a governance document with no machine-readable form.
//   * Settings needs a settings screen.
//
// A palette that silently reaches two of five trains a reader that the missing
// three do not exist. One that names them trains them to ask again later.

import type { UniverseSymbol } from '@entities/market/types'

export interface Command {
  readonly id: string
  readonly group: string
  readonly label: string
  /** Extra words that should match, but are not shown as the label. */
  readonly keywords: string
  /** Shown to the right: why this result is what it is. */
  readonly detail: string
}

export const NOT_REACHED = [
  'Signals by id — needs the consolidated search resource (API Spec amendment)',
  'Doctrine concepts — the SLS glossary is not machine-readable yet',
  'Settings — there is no settings screen',
] as const

export function screenCommands(views: readonly { id: string; label: string }[]): Command[] {
  return views.map((view) => ({
    id: `screen:${view.id}`,
    group: 'Screens',
    label: view.label,
    keywords: view.id,
    detail: 'Go to screen',
  }))
}

export function symbolCommands(rows: readonly UniverseSymbol[]): Command[] {
  return rows.map((row) => ({
    id: `symbol:${row.symbol}`,
    group: 'Symbols',
    label: row.symbol,
    // The base asset, so "bitcoin" finds BTCUSDT the way a reader expects.
    keywords: `${row.base_asset} ${row.quote_asset}`,
    // §21.12 wants live context here -- price tick and signal chips. Neither is
    // on the universe row. Status and tier are, and they are the honest answer
    // to "why can I not find a signal for this one": a QUARANTINE symbol is not
    // being scanned.
    detail: `${row.status} · ${row.tier}`,
  }))
}

/**
 * Subsequence match, ranked by where the run starts.
 *
 * Not fuzzy in the Levenshtein sense and not trying to be. "btc" should find
 * BTCUSDT and "chart" should find Chart; a scoring model that also forgives
 * typos would start returning things the reader did not ask for, in a list
 * they navigate by muscle memory.
 */
export function search(commands: readonly Command[], query: string): Command[] {
  const needle = query.trim().toLowerCase()

  if (needle === '') return [...commands]

  const scored: { command: Command; score: number }[] = []

  for (const command of commands) {
    const haystack = `${command.label} ${command.keywords}`.toLowerCase()
    const score = rank(haystack, command.label.toLowerCase(), needle)

    if (score !== null) scored.push({ command, score })
  }

  // Equal scores keep the order they were supplied in -- the order the groups
  // were built in -- because `Array.prototype.sort` is required to be stable.
  // An explicit index tiebreak was written here and removed: mutating it away
  // changed no test, and it could not, because it was doing nothing the engine
  // was not already guaranteeing.
  return scored.sort((a, b) => a.score - b.score).map((entry) => entry.command)
}

function rank(haystack: string, label: string, needle: string): number | null {
  if (label.startsWith(needle)) return 0
  if (label.includes(needle)) return 1
  if (haystack.includes(needle)) return 2

  return subsequence(haystack, needle) ? 3 : null
}

function subsequence(haystack: string, needle: string): boolean {
  let at = 0

  for (const character of needle) {
    at = haystack.indexOf(character, at)

    if (at === -1) return false

    at += 1
  }

  return true
}
