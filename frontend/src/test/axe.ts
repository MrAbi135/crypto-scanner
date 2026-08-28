// Running axe against a rendered tree (Roadmap S13a DoD: "a11y (axe) clean").
//
// **What this proves, and what it does not.** axe under jsdom checks the
// accessibility tree the markup describes: roles, names, relationships,
// contrast where colours are resolvable. It cannot check what a browser
// computes -- focus order under real layout, contrast from a stylesheet jsdom
// never applied, or whether a screen reader actually says something useful.
// A clean run here means the markup does not contain the failures axe knows
// how to name, which is a floor and not a ceiling.
//
// Stated because "axe clean" is the kind of phrase that gets read as "the
// screen is accessible", and the gap between those two is where a11y work
// stops happening.

import axe, { type AxeResults, type Result, type RunOptions } from 'axe-core'

export interface Violation {
  readonly id: string
  readonly impact: string
  readonly help: string
  readonly nodes: readonly string[]
}

/**
 * Run axe over `container` and return the violations, most severe first.
 *
 * Returns them rather than asserting, so the caller decides what counts. A
 * helper that threw would make every call site's failure message the same
 * sentence, and the useful part of an axe failure is which rule and which node.
 */
export async function axeViolations(
  container: Element,
  options: RunOptions = {},
): Promise<readonly Violation[]> {
  const results: AxeResults = await axe.run(container, {
    // jsdom applies no stylesheet, so every colour resolves to the same
    // transparent default and the contrast rule reports failures that say
    // nothing about the real page. Disabled here and owed a browser check --
    // see the note at the top of this file.
    rules: { 'color-contrast': { enabled: false } },
    ...options,
  })

  return results.violations
    .map(toViolation)
    .sort((a, b) => severity(b.impact) - severity(a.impact))
}

/** A one-line-per-violation summary, for an assertion message worth reading. */
export function describeViolations(violations: readonly Violation[]): string {
  return violations
    .map((v) => `${v.impact}: ${v.id} — ${v.help}\n    ${v.nodes.join('\n    ')}`)
    .join('\n')
}

function toViolation(result: Result): Violation {
  return {
    id: result.id,
    impact: result.impact ?? 'unknown',
    help: result.help,
    nodes: result.nodes.map((node) => node.html),
  }
}

function severity(impact: string): number {
  return { critical: 4, serious: 3, moderate: 2, minor: 1 }[impact] ?? 0
}
