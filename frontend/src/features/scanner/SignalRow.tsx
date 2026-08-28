// Blueprint C1, row projection: one live signal, dense.
//
// C1 lists what a row carries -- symbol+TF, direction glyph, archetype chip,
// grade badge, confidence bar+number, entry/invalidation/target ladder, TTL
// decay. What follows is that list and not more; the card projection, the tick
// animation and the column shedding are C8's and a later piece's.

import type { FeedRow } from '@entities/market/types'

export interface SignalRowProps {
  readonly row: FeedRow
  /**
   * Open this row's own context on the chart.
   *
   * Until this existed, a reader who saw `ETHUSDT H1` here had to switch tabs
   * and retype both -- which is a transcription step between a signal and the
   * evidence for it, in a product whose entire claim is that the evidence is
   * one click away.
   */
  readonly onOpenChart?: ((symbol: string, timeframe: string) => void) | undefined
}

export function SignalRow({ row, onOpenChart }: SignalRowProps) {
  const long = row.direction === 'UP'

  return (
    <tr className="signal" data-testid={`signal-${row.signal_id}`} data-grade={row.grade}>
      <td className="signal__rank">{row.rank}</td>

      <th scope="row" className="signal__symbol">
        {row.symbol}
        <span className="signal__tf">{row.timeframe}</span>
      </th>

      <td className={`signal__direction signal__direction--${long ? 'up' : 'down'}`}>
        {/* The glyph is decorative; the word is the value. A direction carried
            only by an arrow is unreadable to a screen reader and ambiguous to
            anyone who has not learned which way this product points them. */}
        <span aria-hidden="true">{long ? '▲' : '▼'}</span> {long ? 'Long' : 'Short'}
      </td>

      <td className="signal__archetype">{row.archetype}</td>

      {/* C4: letter plus its meaning. §9.4's bands are what a letter *is*. */}
      <td className="signal__grade">
        <span className={`grade grade--${row.grade.toLowerCase()}`}>{row.grade}</span>
      </td>

      <Confidence recorded={row.confidence} displayed={row.display_rank} age={row.age_candles} />

      <td className="signal__ladder">
        {/* Entry, invalidation, target -- the three prices a reader needs to
            judge the row. Strings throughout: these are canonical decimals and
            formatting one here would undo the reason it is a string (API §5). */}
        <dl>
          <div>
            <dt>entry</dt>
            <dd>
              {row.entry.proximal} – {row.entry.distal}
            </dd>
          </div>
          <div>
            <dt>invalid</dt>
            <dd>{row.invalidation}</dd>
          </div>
          <div>
            <dt>target</dt>
            <dd>{row.targets.primary === null ? '—' : row.targets.primary.high}</dd>
          </div>
        </dl>
      </td>

      <td className="signal__ttl">
        {/* §12's lifetime, as elapsed of total rather than a bare countdown: a
            signal 20 candles into a 24-candle life and one 2 into a 6 are both
            "4 left" and are not the same thing. */}
        {row.age_candles}/{row.ttl_candles}
      </td>

      <td className="signal__open">
        {onOpenChart !== undefined && (
          <button
            type="button"
            className="signal__chart"
            data-testid={`open-${row.signal_id}`}
            // Named, not "Open": a table of twenty identical "Open" buttons is
            // a screen reader's list of twenty identical destinations.
            aria-label={`Open ${row.symbol} ${row.timeframe} on the chart`}
            onClick={() => onOpenChart(row.symbol, row.timeframe)}
          >
            Chart
          </button>
        )}
      </td>
    </tr>
  )
}

/**
 * Blueprint C3. SLS §15.4: confidence "is displayed with its factor breakdown
 * — never as a bare number".
 *
 * The breakdown itself is C2's and needs the evidence row, which the feed does
 * not carry. What this can do without it is refuse to show one number as
 * though it were the whole story: the recorded score and the decayed one are
 * both here and labelled, because §9.3 moves one and not the other, and a
 * reader given only the decayed figure cannot tell a weakening signal from a
 * weak one.
 */
function Confidence({
  recorded,
  displayed,
  age,
}: {
  readonly recorded: string
  readonly displayed: string
  readonly age: number
}) {
  // Width only. The number beside it is the value; this is the glance.
  const width = `${clampPercent(displayed)}%`

  return (
    <td className="signal__confidence">
      <div
        className="meter"
        role="img"
        aria-label={`confidence ${recorded}, displayed ${displayed} after ${age} candles`}
      >
        <div className="meter__fill" style={{ width }} />
      </div>

      <span className="meter__value">{displayed}</span>

      {recorded !== displayed && (
        <span className="meter__recorded" title="recorded at publication; §9.3 decays the display">
          was {recorded}
        </span>
      )}
    </td>
  )
}

/**
 * A percentage for the bar, and nothing else reads this.
 *
 * `Number` appears here and in no other place in this file. A confidence is a
 * decimal string for the same reason a price is, and the only thing allowed to
 * turn one into a float is a pixel width -- where the rounding cannot reach
 * anything a reader is told.
 */
function clampPercent(value: string): number {
  const parsed = Number(value)

  if (!Number.isFinite(parsed)) return 0

  return Math.min(100, Math.max(0, parsed))
}
