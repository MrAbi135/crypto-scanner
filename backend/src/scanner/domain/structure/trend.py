"""Structure trend state machine (SLS §3.4/§3.6)."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from enum import Enum

from scanner.domain.structure.breaks import BreakDirection


class TrendState(str, Enum):
    RANGING = "RANGING"
    BULLISH = "BULLISH"
    BULLISH_CAUTION = "BULLISH_CAUTION"
    BEARISH = "BEARISH"
    BEARISH_CAUTION = "BEARISH_CAUTION"


@dataclass(slots=True)
class TrendStateMachine:
    """Deterministic structure-trend state."""

    state: TrendState = TrendState.RANGING

    def apply_choch(
        self,
        direction: BreakDirection,
    ) -> TrendState:
        """CHoCH warns; it never directly flips prevailing trend."""

        if self.state is TrendState.BULLISH and direction is BreakDirection.DOWN:
            self.state = TrendState.BULLISH_CAUTION

        elif self.state is TrendState.BEARISH and direction is BreakDirection.UP:
            self.state = TrendState.BEARISH_CAUTION

        return self.state

    def apply_mss(
        self,
        direction: BreakDirection,
    ) -> TrendState:
        """Only confirmed MSS flips the prevailing trend."""

        if self.state is TrendState.BULLISH_CAUTION and direction is BreakDirection.DOWN:
            self.state = TrendState.BEARISH

        elif self.state is TrendState.BEARISH_CAUTION and direction is BreakDirection.UP:
            self.state = TrendState.BULLISH

        return self.state

    def fail_mss_candidate(self) -> TrendState:
        """Failed follow-through restores the pre-CHoCH trend."""

        if self.state is TrendState.BULLISH_CAUTION:
            self.state = TrendState.BULLISH

        elif self.state is TrendState.BEARISH_CAUTION:
            self.state = TrendState.BEARISH

        return self.state


# §3.4: "RANGING additionally applies when price has closed inside the current
# external dealing range without external BOS for
# `P.structure.idle_candles = 100` closed candles", and the state diagram's
# `BULLISH --> RANGING: structure idle 100 candles`.
IDLE_CANDLES = 100


def structure_is_idle(
    closes: Sequence[Decimal],
    *,
    range_low: Decimal,
    range_high: Decimal,
    broke_externally: bool,
    idle_candles: int = IDLE_CANDLES,
) -> bool:
    """§3.4's second route into RANGING.

    Two conditions, both over the same window: every close inside the current
    external dealing range, and no external BOS. A trend that is still
    breaking levels is not idle however narrow its range, and a market that
    has left the bracket is not idle however quiet it has been since.

    `closes` is the whole series; only its last `idle_candles` are read. Fewer
    than that and the answer is False -- not because the market is busy but
    because the question has not been asked long enough to answer, and
    treating "too early to tell" as "idle" would put every young series into
    RANGING the moment it earned a trend.

    The bracket is §5.7's, and §5.7 says both its anchors "are confirmed-swing
    facts" -- structure's own. `dealing_range_at` in the ICT layer builds the
    same two anchors and adds premium/discount on top; it cannot be reused
    here because `structure` sits below `ict` and must, so this reads the
    bracket it is handed rather than computing a second definition of it.
    """
    if idle_candles <= 0:
        raise ValueError("idle_candles must be positive")

    if range_high < range_low:
        raise ValueError("range_high must be >= range_low")

    if broke_externally:
        return False

    if len(closes) < idle_candles:
        return False

    return all(range_low <= close <= range_high for close in closes[-idle_candles:])
