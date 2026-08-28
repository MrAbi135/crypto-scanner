// Blueprint §21.19's quiet-feed empty.
//
// The Blueprint calls this "the single most important" empty state and gives
// the reason: it "converts the scariest moment (paying for silence) into
// doctrine proof". A user looking at an empty board is asking whether the
// product is broken or the market is quiet, and those look identical unless
// the screen says which.
//
// The three-part anatomy is binding and in this order: reason line, context
// line, primary action. So is the tone -- §21.19 says empties "never blame the
// user, never render as failure red, and never fake urgency".

export interface QuietFeedProps {
  /**
   * Live signals before any filtering, from the feed's own `page.live_total`.
   *
   * Not the below-floor count. That number belongs to §18.6's rankings board,
   * which answers what one timeframe offered at one close; the feed answers
   * what is on the table now across all of them and does not carry it. Saying
   * "sixty-four scored and stopped at the floor" here would be a number this
   * screen has not been told -- plausible, unsourced, and exactly the kind of
   * thing an honesty surface must not do.
   */
  readonly liveTotal: number
  /** Whether the emptiness could be the reader's own filter. */
  readonly filtered: boolean
  readonly onShowFloors: () => void
  readonly onClearFilter: () => void
}

export function QuietFeed({
  liveTotal,
  filtered,
  onShowFloors,
  onClearFilter,
}: QuietFeedProps) {
  // A filter hiding live rows is a different empty from a quiet market, and
  // §21.19 catalogs them separately -- "empty history filter (state the
  // filter, offer clear)" against the quiet feed. Rendering one as the other
  // would tell a user the market is silent when their own chip is.
  const hiddenByFilter = filtered && liveTotal > 0

  return (
    <div className="quiet" data-testid="quiet-feed" role="status">
      <p className="quiet__reason">
        {hiddenByFilter ? 'No signals match this filter.' : 'No qualifying setups — floors held.'}
      </p>

      <p className="quiet__context">
        {hiddenByFilter
          ? `${liveTotal} signal${liveTotal === 1 ? ' is' : 's are'} live and hidden by it.`
          : 'Nothing is live. Silence is the doctrine holding, not the scanner failing — §8.6 publishes a setup only above its archetype floor.'}
      </p>

      {/* Exactly one primary action, and it never dead-ends (§21.19). */}
      {hiddenByFilter ? (
        <button type="button" className="quiet__action" onClick={onClearFilter}>
          Clear the filter
        </button>
      ) : (
        <button type="button" className="quiet__action" onClick={onShowFloors}>
          See the floors
        </button>
      )}
    </div>
  )
}
