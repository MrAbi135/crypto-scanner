"""Premium/Discount dealing-range context (SLS §5.7)."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from enum import Enum

from scanner.domain.structure import SwingKind, SwingPoint, swing_window

_ZERO = Decimal("0")
_HALF = Decimal("0.5")
_EXTREME_THIRD = Decimal("0.33")
_UPPER_EXTREME_THIRD = Decimal("0.67")
_MIN_RANGE_ATR = Decimal("1.5")


class PdState(str, Enum):
    PREMIUM = "PREMIUM"
    DISCOUNT = "DISCOUNT"
    AT_EQ = "AT_EQ"
    SUSPENDED = "PD_SUSPENDED"


@dataclass(frozen=True, slots=True)
class DealingRange:
    range_id: str
    low: Decimal
    high: Decimal
    low_anchor_index: int
    high_anchor_index: int

    def __post_init__(self) -> None:
        if self.high <= self.low:
            raise ValueError("dealing range high must be greater than low")

        if self.low_anchor_index < 0:
            raise ValueError("low anchor index must be non-negative")

        if self.high_anchor_index < 0:
            raise ValueError("high anchor index must be non-negative")

    @property
    def height(self) -> Decimal:
        return self.high - self.low

    @property
    def equilibrium(self) -> Decimal:
        return self.low + self.height / Decimal("2")


@dataclass(frozen=True, slots=True)
class PdContext:
    range_id: str
    state: PdState
    range_low: Decimal
    range_high: Decimal
    equilibrium: Decimal
    close: Decimal
    range_position: Decimal | None
    long_gate: bool
    short_gate: bool
    sweep_long_gate: bool
    sweep_short_gate: bool


def evaluate_pd_context(
    dealing_range: DealingRange,
    *,
    close: Decimal,
    atr: Decimal,
    epsilon: Decimal = Decimal("0"),
) -> PdContext:
    """Evaluate premium/discount context for one closed candle."""

    if atr <= _ZERO:
        raise ValueError("atr must be positive")

    if epsilon < _ZERO:
        raise ValueError("epsilon must be non-negative")

    if dealing_range.height < _MIN_RANGE_ATR * atr:
        return PdContext(
            range_id=dealing_range.range_id,
            state=PdState.SUSPENDED,
            range_low=dealing_range.low,
            range_high=dealing_range.high,
            equilibrium=(dealing_range.equilibrium),
            close=close,
            range_position=None,
            long_gate=False,
            short_gate=False,
            sweep_long_gate=False,
            sweep_short_gate=False,
        )

    raw_position = (close - dealing_range.low) / dealing_range.height

    clamped = min(
        Decimal("1"),
        max(
            Decimal("0"),
            raw_position,
        ),
    )

    # §0.4: "quantisation is a presentation rule, never a decision rule, so
    # it cannot change a verdict." The recorded position is quantised; every
    # gate below is derived from the UNQUANTISED value -- deriving them from
    # the quantised one flipped verdicts at the boundaries (0.50004 rounds to
    # 0.5000 and opened the long gate the raw comparison refuses).
    range_position = clamped.quantize(Decimal("0.0001"))

    equilibrium = dealing_range.equilibrium

    if close > equilibrium + epsilon:
        state = PdState.PREMIUM
    elif close < equilibrium - epsilon:
        state = PdState.DISCOUNT
    else:
        state = PdState.AT_EQ

    return PdContext(
        range_id=dealing_range.range_id,
        state=state,
        range_low=dealing_range.low,
        range_high=dealing_range.high,
        equilibrium=equilibrium,
        close=close,
        range_position=range_position,
        long_gate=(clamped <= _HALF),
        short_gate=(clamped >= _HALF),
        sweep_long_gate=(clamped <= _EXTREME_THIRD),
        sweep_short_gate=(clamped >= _UPPER_EXTREME_THIRD),
    )


def dealing_range_at(
    swings: Sequence[SwingPoint],
    *,
    close: Decimal,
    index: int,
    range_id: str | None = None,
) -> DealingRange | None:
    """§5.7's dealing range as of `index`.

    "Most recent confirmed external swing low <-> external swing high that
    bracket current price." Most recent on each side independently, because the
    range re-anchors "whenever a new external swing confirms" and a new high
    does not invalidate the low it is measured against.

    Returns None when either side has no swing yet, or when price has left the
    bracket -- which is not a failure but §5.7's own condition: a range price
    is outside is not the range price is in.
    """
    # Confirmed as of `index`, not merely pivoted: §3.1 dates a swing's
    # existence to the close of its k-th follow-up candle and says "no
    # downstream logic may consume it earlier". Filtering on the pivot alone
    # let a replay anchor ranges on swings a live engine could not yet see --
    # up to k_ext = 5 candles of look-ahead, a §0.2 non-repaint break. The
    # rule lives here, at the root, so every caller inherits it.
    eligible = [swing for swing in swings if swing.index + swing_window(swing.strength) <= index]

    highs = [swing for swing in eligible if swing.kind is SwingKind.HIGH]
    lows = [swing for swing in eligible if swing.kind is SwingKind.LOW]

    if not highs or not lows:
        return None

    high = max(highs, key=lambda swing: swing.index)
    low = max(lows, key=lambda swing: swing.index)

    return bracketed_dealing_range(
        range_id=range_id if range_id is not None else f"{low.index}:{high.index}",
        external_low=low.price,
        external_high=high.price,
        low_anchor_index=low.index,
        high_anchor_index=high.index,
        close=close,
    )


def bracketed_dealing_range(
    *,
    range_id: str,
    external_low: Decimal,
    external_high: Decimal,
    low_anchor_index: int,
    high_anchor_index: int,
    close: Decimal,
) -> DealingRange | None:
    """Build range only when current close is bracketed by both anchors."""

    if external_high <= external_low:
        return None

    if not (external_low <= close <= external_high):
        return None

    return DealingRange(
        range_id=range_id,
        low=external_low,
        high=external_high,
        low_anchor_index=(low_anchor_index),
        high_anchor_index=(high_anchor_index),
    )
