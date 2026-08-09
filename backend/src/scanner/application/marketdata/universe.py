"""Universe tier classification (SLS §1.4, Sprint S3)."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import Enum

from scanner.shared import Timeframe


class UniverseTier(str, Enum):
    """Liquidity eligibility tier."""

    T1 = "T1"
    T2 = "T2"
    T3 = "T3"
    INELIGIBLE = "INELIGIBLE"


@dataclass(frozen=True, slots=True)
class LiquiditySnapshot:
    """Seven-day median liquidity metrics for one symbol."""

    median_daily_quote_volume: Decimal
    median_spread_bps: Decimal
    median_depth_2pct: Decimal


_T1_MIN_VOLUME = Decimal("100000000")
_T1_MAX_SPREAD_BPS = Decimal("2")
_T1_MIN_DEPTH = Decimal("1000000")

_T2_MIN_VOLUME = Decimal("20000000")
_T2_MAX_SPREAD_BPS = Decimal("5")
_T2_MIN_DEPTH = Decimal("250000")

_T3_MIN_VOLUME = Decimal("5000000")
_T3_MAX_SPREAD_BPS = Decimal("10")
_T3_MIN_DEPTH = Decimal("100000")


def classify_tier(snapshot: LiquiditySnapshot) -> UniverseTier:
    """Classify one symbol using the SLS §1.4 liquidity thresholds."""

    if (
        snapshot.median_daily_quote_volume >= _T1_MIN_VOLUME
        and snapshot.median_spread_bps <= _T1_MAX_SPREAD_BPS
        and snapshot.median_depth_2pct >= _T1_MIN_DEPTH
    ):
        return UniverseTier.T1

    if (
        snapshot.median_daily_quote_volume >= _T2_MIN_VOLUME
        and snapshot.median_spread_bps <= _T2_MAX_SPREAD_BPS
        and snapshot.median_depth_2pct >= _T2_MIN_DEPTH
    ):
        return UniverseTier.T2

    if (
        snapshot.median_daily_quote_volume >= _T3_MIN_VOLUME
        and snapshot.median_spread_bps <= _T3_MAX_SPREAD_BPS
        and snapshot.median_depth_2pct >= _T3_MIN_DEPTH
    ):
        return UniverseTier.T3

    return UniverseTier.INELIGIBLE


def scanned_timeframes(tier: UniverseTier) -> tuple[Timeframe, ...]:
    """Return the scanned timeframe set permitted by one liquidity tier."""

    if tier is UniverseTier.T1:
        return (
            Timeframe.M5,
            Timeframe.M15,
            Timeframe.H1,
            Timeframe.H4,
            Timeframe.D1,
            Timeframe.W1,
        )

    if tier is UniverseTier.T2:
        return (
            Timeframe.M15,
            Timeframe.H1,
            Timeframe.H4,
            Timeframe.D1,
            Timeframe.W1,
        )

    if tier is UniverseTier.T3:
        return (
            Timeframe.H1,
            Timeframe.H4,
            Timeframe.D1,
            Timeframe.W1,
        )

    return ()


def alert_eligible(tier: UniverseTier) -> bool:
    """Whether the tier is eligible for any alerts."""

    return tier is not UniverseTier.INELIGIBLE
