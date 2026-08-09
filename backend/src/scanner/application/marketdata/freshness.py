"""Market-data freshness state model (SLS §2.12, §2.15)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum

from scanner.shared import Timeframe

_FRESH_LAG = timedelta(seconds=2)
_STALE_LAG = timedelta(seconds=5)
_DEAD_LAG = timedelta(seconds=30)

_RECOVERY_CANDLES = 20


class FreshnessState(str, Enum):
    """Operational freshness state for one symbol-timeframe feed."""

    FRESH = "FRESH"
    STALE = "STALE"
    SUSPECT = "SUSPECT"
    DEGRADED = "DEGRADED"


@dataclass(slots=True)
class FreshnessTracker:
    """Track freshness and recovery state for one symbol-timeframe series."""

    symbol: str
    timeframe: Timeframe
    state: FreshnessState = FreshnessState.FRESH
    recovery_candles: int = 0
    last_event_at: datetime | None = None

    def observe_event(
        self,
        *,
        event_at: datetime,
        observed_at: datetime,
    ) -> FreshnessState:
        """Update freshness from exchange-event to local-observation lag."""
        self.last_event_at = event_at

        lag = observed_at - event_at

        if lag < timedelta(0):
            self.state = FreshnessState.SUSPECT
            self.recovery_candles = 0
            return self.state

        if lag > _DEAD_LAG:
            self.state = FreshnessState.DEGRADED
            self.recovery_candles = 0
            return self.state

        if lag > _STALE_LAG:
            self.state = FreshnessState.STALE
            self.recovery_candles = 0
            return self.state

        if lag <= _FRESH_LAG and self.state not in {
            FreshnessState.DEGRADED,
            FreshnessState.SUSPECT,
        }:
            self.state = FreshnessState.FRESH

        return self.state

    def mark_suspect(self) -> FreshnessState:
        """Quarantine the series after a validation or sanity concern."""
        self.state = FreshnessState.SUSPECT
        self.recovery_candles = 0
        return self.state

    def mark_degraded(self) -> FreshnessState:
        """Suspend trustworthy detection after an unfillable data gap."""
        self.state = FreshnessState.DEGRADED
        self.recovery_candles = 0
        return self.state

    def record_verified_candle(self) -> FreshnessState:
        """Advance recovery after a continuously verified closed candle."""
        if self.state not in {
            FreshnessState.DEGRADED,
            FreshnessState.SUSPECT,
        }:
            return self.state

        self.recovery_candles += 1

        if self.recovery_candles >= _RECOVERY_CANDLES:
            self.state = FreshnessState.FRESH
            self.recovery_candles = 0

        return self.state

    def break_recovery(self) -> FreshnessState:
        """Reset a recovery streak when continuity is broken."""
        self.recovery_candles = 0
        return self.state

    @property
    def detection_allowed(self) -> bool:
        """Whether downstream detectors may consume this series."""
        return self.state is FreshnessState.FRESH
