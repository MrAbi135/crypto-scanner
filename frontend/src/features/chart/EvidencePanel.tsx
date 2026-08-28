// The panel that answers "why does the engine say that?" (Roadmap S13a).

import type { Fact, Inspection } from '@features/chart/inspection'

export interface EvidencePanelProps {
  readonly inspection: Inspection | null
  readonly onClose: () => void
}

export function EvidencePanel({ inspection, onClose }: EvidencePanelProps) {
  if (inspection === null) {
    return (
      <aside className="evidence evidence--empty" data-testid="evidence-empty">
        <p>Select an object on the chart to see the evidence the engine stored for it.</p>
      </aside>
    )
  }

  return (
    <aside
      className="evidence"
      data-testid="evidence"
      data-kind={inspection.kind}
      // A live region: selection happens on the chart, so a screen reader user
      // who activates a marker is not looking here and would otherwise get no
      // acknowledgement that anything happened.
      aria-live="polite"
      aria-label={`Evidence for ${inspection.title}`}
    >
      <header className="evidence__header">
        <h2 className="evidence__title">{inspection.title}</h2>
        <button type="button" className="evidence__close" onClick={onClose}>
          Close
        </button>
      </header>

      <Facts caption="Recorded" facts={inspection.facts} testId="evidence-facts" />

      {inspection.evidence.length > 0 ? (
        <Facts caption="Evidence" facts={inspection.evidence} testId="evidence-payload" />
      ) : (
        // Said out loud. An object whose evidence blob is empty and one whose
        // panel failed to render it look identical otherwise, and §15.2
        // requires every object to carry its evidence -- so an empty one is a
        // finding, not a blank space.
        <p className="evidence__none" data-testid="evidence-none">
          The engine stored no evidence payload for this object.
        </p>
      )}
    </aside>
  )
}

function Facts({
  caption,
  facts,
  testId,
}: {
  readonly caption: string
  readonly facts: readonly Fact[]
  readonly testId: string
}) {
  if (facts.length === 0) return null

  return (
    <table className="evidence__table" data-testid={testId}>
      <caption className="evidence__caption">{caption}</caption>
      <tbody>
        {facts.map((fact) => (
          <tr key={fact.label}>
            <th scope="row">{fact.label}</th>
            {/* Values are shown verbatim, in a monospaced cell. A price is a
                canonical decimal string and reformatting it here would undo
                the reason it is a string (API §5). */}
            <td className="evidence__value">{fact.value}</td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}
