"""§12.4's outcome accounting: MFE and MAE in R (SLS §12.4)."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal

from scanner.domain.confluence import SignalLevels
from scanner.domain.lifecycle.state import Candle, SignalState

# §12.4: "Expired states are excluded from hit-rate but reported (a scanner
# that times out constantly has a target-selection problem -- visible, not
# hidden)."
HIT_RATE_STATES = frozenset({SignalState.SUCCESS, SignalState.FAILED})


@dataclass(frozen=True, slots=True)
class Outcome:
    """One resolved signal's terminal accounting."""

    outcome: SignalState
    elapsed_candles: int
    mfe_r: Decimal
    mae_r: Decimal

    @property
    def counts_toward_hit_rate(self) -> bool:
        """§12.4: expired signals are reported but excluded from the rate."""

        return self.outcome in HIT_RATE_STATES


def accounting(
    outcome: SignalState,
    *,
    levels: SignalLevels,
    candles: Sequence[Candle],
) -> Outcome:
    """§12.4's "max favorable excursion (MFE) and max adverse excursion (MAE)
    in R units (R = |entry mid - invalidation|)".

    Computed from the candles between publication and resolution rather than
    accumulated as the signal runs. An accumulator would have to be updated on
    a table with no UPDATE surface, and a monitor that missed a candle would
    under-report the excursion for the rest of the signal's life -- silently,
    and in the direction that flatters the record.

    Excursion is measured from the **entry mid**, the same origin R is, so the
    two are commensurable. Measuring favourable travel from the proximal edge
    and adverse travel from the distal one would quietly widen every winner
    and narrow every loser by the width of the band.

    Both are floored at zero. A signal that never traded in its favour has an
    MFE of zero, not a negative one -- "the furthest it went the right way"
    cannot be less than nowhere.
    """
    unit = abs(levels.entry.mid - levels.invalidation.price)

    if unit <= 0:
        raise ValueError("R is zero: the invalidation sits on the entry mid")

    if not candles:
        return Outcome(outcome, 0, Decimal(0), Decimal(0))

    mid = levels.entry.mid

    if levels.direction == "UP":
        favourable = max(c.high for c in candles) - mid
        adverse = mid - min(c.low for c in candles)
    else:
        favourable = mid - min(c.low for c in candles)
        adverse = max(c.high for c in candles) - mid

    return Outcome(
        outcome=outcome,
        elapsed_candles=len(candles),
        mfe_r=max(Decimal(0), favourable) / unit,
        mae_r=max(Decimal(0), adverse) / unit,
    )
