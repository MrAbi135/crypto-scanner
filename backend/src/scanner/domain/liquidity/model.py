"""Liquidity-domain primitives (SLS §4, Sprint S5)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import Enum

# §4.6: "a sweep's setup relevance expires P.liquidity.sweep_expiry = 15
# closed candles after confirmation".
SWEEP_SETUP_EXPIRY_CANDLES = 15


class LiquiditySide(str, Enum):
    BSL = "BSL"
    SSL = "SSL"


class LiquidityClass(str, Enum):
    INTERNAL = "INTERNAL"
    EXTERNAL = "EXTERNAL"


class PoolSource(str, Enum):
    SWING = "SWING"
    CLUSTER = "CLUSTER"
    RANGE = "RANGE"


class PoolState(str, Enum):
    ACTIVE = "ACTIVE"
    SWEPT = "SWEPT"
    BROKEN = "BROKEN"
    EXPIRED = "EXPIRED"


@dataclass(frozen=True, slots=True)
class PoolStrength:
    touches_component: Decimal
    timeframe_component: Decimal
    age_component: Decimal
    cluster_component: Decimal

    @property
    def total(self) -> Decimal:
        value = (
            self.touches_component
            + self.timeframe_component
            + self.age_component
            + self.cluster_component
        )
        return min(Decimal("100"), max(Decimal("0"), value))


@dataclass(frozen=True, slots=True)
class LiquidityPool:
    pool_id: str
    side: LiquiditySide
    liquidity_class: LiquidityClass
    source: PoolSource
    price: Decimal
    band_low: Decimal
    band_high: Decimal
    created_at: datetime
    created_index: int
    strength: PoolStrength
    state: PoolState = PoolState.ACTIVE
    member_count: int = 1

    @property
    def sweep_level(self) -> Decimal:
        if self.side is LiquiditySide.BSL:
            return self.band_high
        return self.band_low


@dataclass(frozen=True, slots=True)
class EqualLevelCluster:
    cluster_id: str
    side: LiquiditySide
    member_indices: tuple[int, ...]
    member_prices: tuple[Decimal, ...]
    band_low: Decimal
    band_high: Decimal
    # §4.3: the candle at which the second member's swing confirms. Later
    # members append, so this is the cluster's birth stamp and does not move.
    confirmed_index: int

    @property
    def member_count(self) -> int:
        return len(self.member_indices)

    @property
    def extreme(self) -> Decimal:
        """§4.2: a cluster pool's price is the extreme of its members."""
        return self.band_high if self.side is LiquiditySide.BSL else self.band_low


@dataclass(frozen=True, slots=True)
class SweepEvent:
    pool_id: str
    side: LiquiditySide
    liquidity_class: LiquidityClass
    confirmed_at: datetime
    confirmed_index: int
    penetration_price: Decimal
    reference_level: Decimal
    close_back_price: Decimal
    sweep_depth_atr: Decimal
    confirmation_window: int
    gap_sweep: bool
    reclaimed: bool = False
    displaced_after: bool = False

    @property
    def setup_expiry_index(self) -> int:
        return self.confirmed_index + SWEEP_SETUP_EXPIRY_CANDLES


@dataclass(frozen=True, slots=True)
class StopHuntEvent:
    sweep_pool_id: str
    confirmed_at: datetime
    confirmed_index: int
    displacement_id: str
    elapsed_candles: int
    failed: bool = False
