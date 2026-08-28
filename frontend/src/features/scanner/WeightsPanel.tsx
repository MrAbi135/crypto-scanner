// §9.1's weight table, published (API §18.6's "doctrine transparency
// endpoint", PRD FC-4.1).
//
// This is where "See the floors" lands, and it is the reason that action
// exists: §21.19 says an empty never dead-ends, and the honest answer to "why
// is the board empty" is the floors themselves.
//
// **The justification is the doctrine's own prose.** The endpoint transcribes
// §9.1 rather than summarising it, and so does this: a paraphrase would let
// the published reason drift from the rule it defends, and a reader has no way
// to tell the difference. Nothing here shortens, truncates or "cleans up" the
// text it was given.

import type { Weights } from '@entities/market/types'

export interface WeightsPanelProps {
  readonly weights: Weights | null
  readonly error: string | null
}

export function WeightsPanel({ weights, error }: WeightsPanelProps) {
  if (error !== null) {
    return (
      <p role="alert" data-testid="weights-error">
        {error}
      </p>
    )
  }

  if (weights === null) {
    return (
      <p role="status" data-testid="weights-loading">
        Loading the weights…
      </p>
    )
  }

  return (
    <section className="weights" data-testid="weights">
      <h2 className="weights__title">How confidence is scored</h2>

      <table className="weights__table">
        <caption>
          §9.1, param set {weights.param_set_version}
          {/* The version travels with the table because §9.1 makes the weights
              `P.rank.weights`, versioned -- a table without its version cannot
              be matched to the signals it scored. */}
        </caption>
        <thead>
          <tr>
            <th scope="col">Factor</th>
            <th scope="col">Weight</th>
            <th scope="col">Why</th>
          </tr>
        </thead>
        <tbody>
          {weights.factors.map((factor) => (
            <tr key={factor.factor}>
              <th scope="row">
                {factor.factor} {factor.name}
              </th>
              <td className="weights__pct">{factor.weight_pct}%</td>
              <td className="weights__why">{factor.justification}</td>
            </tr>
          ))}
        </tbody>
      </table>

      <h3 className="weights__subtitle">Grade floors</h3>

      <table className="weights__table" data-testid="grade-bands">
        <tbody>
          {weights.grades.map((band) => (
            <tr key={band.grade}>
              <th scope="row">{band.grade}</th>
              <td className="weights__pct">{band.min_confidence} and above</td>
            </tr>
          ))}
          <tr>
            <th scope="row">below</th>
            {/* §9.4: below the lowest floor is not a weak grade -- it is not
                published. Said in the server's words so a client cannot invent
                a "C" that the doctrine does not have. */}
            <td className="weights__pct">{weights.below_lowest_floor}</td>
          </tr>
        </tbody>
      </table>
    </section>
  )
}
