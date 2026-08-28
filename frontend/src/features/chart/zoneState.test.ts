import { readFileSync } from 'node:fs'
import { join } from 'node:path'

import { describe, expect, it } from 'vitest'

import { TREATED_STATES, isTreated } from '@features/chart/zoneState'

// Read as text rather than imported: the treatments are CSS, jsdom applies no
// stylesheet, and the assertion worth making is about the file that ships.
// Resolved from the vitest root, because `import.meta.url` under the jsdom
// environment is not a `file:` URL.
const CSS = readFileSync(join(process.cwd(), 'src/features/chart/chart.css'), 'utf8')

describe('zone state treatments', () => {
  it.each(TREATED_STATES)('%s has a rule of its own', (state) => {
    // Blueprint §16.3. Without a rule the state inherits the base `.zone` wash,
    // which is the FRESH treatment -- so a state listed here but not styled is
    // a zone that lies about being untouched. That is exactly what shipped
    // before this: `data-state` was rendered and nothing read it.
    expect(CSS).toContain(`.zone[data-state='${state}']`)
  })

  it('separates the five §16.3 steps rather than styling them alike', () => {
    // A list of rules that all set the same values would pass the test above
    // and change nothing on screen. Each step has to differ from the one before
    // it in the property that carries the ordering.
    const opacity = (state: string) => {
      const rule = CSS.slice(CSS.indexOf(`.zone[data-state='${state}']`))

      return /fill-opacity:\s*([\d.]+)/.exec(rule.slice(0, rule.indexOf('}')))?.[1]
    }

    const steps = ['FRESH', 'TESTED', 'MITIGATED', 'INVALIDATED', 'EXPIRED'].map(opacity)

    expect(new Set(steps).size).toBe(steps.length)
  })

  it('does not carry a colour as the only difference', () => {
    // Blueprint §158: colour is never the sole carrier. Each state also moves
    // opacity or the dash pattern, so the scale survives greyscale.
    for (const state of ['TESTED', 'INVALIDATED', 'EXPIRED']) {
      const rule = CSS.slice(CSS.indexOf(`.zone[data-state='${state}']`))
      const body = rule.slice(0, rule.indexOf('}'))

      expect(body, state).toMatch(/stroke-dasharray/)
    }
  })

  it('has a rule for a state nobody has heard of', () => {
    // The one that keeps the fix from decaying. A sixth server-side state would
    // match no rule and inherit FRESH; this class is what it gets instead.
    //
    // Matched with its brace. `toContain('.zone--untreated')` also matches
    // `.zone--untreated-x`, so renaming the rule left the assertion green --
    // found by mutating the rule name, not by reading the line.
    expect(CSS).toMatch(/\.zone--untreated\s*\{/)
  })

  it('covers every state the three server enums can send', () => {
    // Mirrors `ZoneState`, `FvgState` and `IfvgState` in
    // `backend/src/scanner/domain/ict/model.py`. Written out rather than
    // iterated from `TREATED_STATES`, because a test that iterates the list it
    // is checking passes when a state is *deleted* from it -- and a deleted
    // state is a real zone drawn with the loud unknown-state outline.
    //
    // It cannot see the server change. What it can do is refuse a change made
    // here, and say in one place what the server was last known to send.
    const FROM_THE_SERVER = [
      'FRESH',
      'TESTED',
      'MITIGATED',
      'INVALIDATED',
      'EXPIRED',
      'OPEN',
      'TOUCHED',
      'CE_FILLED',
      'FILLED',
      'INVERTED',
      'UNPROVEN',
      'DEAD',
    ]

    for (const state of FROM_THE_SERVER) {
      expect(isTreated(state), state).toBe(true)
    }
  })

  it('recognises both words for untouched', () => {
    // `FRESH` for order blocks, `OPEN` for fair-value gaps. §8.3.1's table was
    // once written in only one of them and every FVG scored zero for spelling
    // it differently.
    expect(isTreated('FRESH')).toBe(true)
    expect(isTreated('OPEN')).toBe(true)
  })

  it('refuses a state it does not know', () => {
    expect(isTreated('SOMETHING_NEW')).toBe(false)
    expect(isTreated('')).toBe(false)
  })
})
