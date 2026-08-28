import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import { EvidencePanel } from './EvidencePanel'
import type { Inspection } from './inspection'

function inspection(overrides: Partial<Inspection> = {}): Inspection {
  return {
    id: 'zone:z1',
    kind: 'zone',
    title: 'OB OB_A — FRESH',
    facts: [{ label: 'band', value: '100.10 – 101.20' }],
    evidence: [{ label: 'mss_origin', value: 'true' }],
    ...overrides,
  }
}

describe('EvidencePanel', () => {
  it('invites a selection rather than showing a blank frame', () => {
    // An empty panel and a broken one look the same otherwise, and this is the
    // screen whose job is to be trustworthy about what it is not showing.
    render(<EvidencePanel inspection={null} onClose={vi.fn()} />)

    expect(screen.getByTestId('evidence-empty')).toBeDefined()
  })

  it('separates the contract facts from the stored payload', () => {
    render(<EvidencePanel inspection={inspection()} onClose={vi.fn()} />)

    expect(screen.getByTestId('evidence-facts').textContent).toContain('100.10 – 101.20')
    expect(screen.getByTestId('evidence-payload').textContent).toContain('mss_origin')
  })

  it('says so when the engine stored no evidence', () => {
    // §15.2 requires every object to carry its evidence, so an empty payload
    // is a finding rather than a blank space -- and a panel that rendered
    // nothing would make a missing payload and a broken panel identical.
    render(<EvidencePanel inspection={inspection({ evidence: [] })} onClose={vi.fn()} />)

    expect(screen.getByTestId('evidence-none')).toBeDefined()
    expect(screen.queryByTestId('evidence-payload')).toBeNull()
  })

  it('announces itself, because selection happens elsewhere', () => {
    // A screen reader user activates a marker on the chart and is not looking
    // at this panel. Without a live region nothing tells them it changed.
    render(<EvidencePanel inspection={inspection()} onClose={vi.fn()} />)

    const panel = screen.getByTestId('evidence')

    expect(panel.getAttribute('aria-live')).toBe('polite')
    expect(panel.getAttribute('aria-label')).toContain('OB OB_A — FRESH')
  })

  it('closes', () => {
    const onClose = vi.fn()

    render(<EvidencePanel inspection={inspection()} onClose={onClose} />)

    fireEvent.click(screen.getByRole('button', { name: 'Close' }))

    expect(onClose).toHaveBeenCalledOnce()
  })

  it('shows a value verbatim', () => {
    // The panel must not reformat. A price is a canonical decimal string and
    // the reason it is one survives only if nothing along the way tidies it.
    render(
      <EvidencePanel
        inspection={inspection({ evidence: [{ label: 'price', value: '62754.000000000000000000' }] })}
        onClose={vi.fn()}
      />,
    )

    expect(screen.getByText('62754.000000000000000000')).toBeDefined()
  })
})
