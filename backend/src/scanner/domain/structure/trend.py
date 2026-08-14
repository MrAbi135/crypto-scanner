"""Structure trend state machine (SLS §3.4/§3.6)."""

from __future__ import annotations

from dataclasses import dataclass
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
