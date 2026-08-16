"""SLS §4 branch coverage: SSL sweep paths, guards, and stop-hunt reversals.

The Sprint S5 suite (`test_liquidity_engine.py`) exercises the BSL happy paths.
This module covers the mirror-image SSL doctrine, every input guard, and the
post-confirmation flag transitions (`reclaimed`, `displaced_after`, `failed`)
that the sweep and stop-hunt state carry.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from scanner.domain.common import Candle, CandleSource
from scanner.domain.liquidity import (
    LiquidityClass,
    LiquidityPool,
    LiquiditySide,
    PoolSource,
    PoolState,
    PoolStrength,
    SweepEvent,
    detect_single_candle_sweep,
    detect_stop_hunt,
    detect_two_candle_sweep,
    mark_stop_hunt_failed,
    pool_from_swing,
    score_pool_strength,
    sweep_reclaimed,
)
from scanner.domain.liquidity.sweeps import mark_displaced_after
from scanner.domain.structure import SwingKind, SwingPoint, SwingStrength
from scanner.shared import Timeframe

ATR = Decimal("2")
EPSILON = Decimal("0.1")
LEVEL = Decimal("100")


def make_candle(
    *,
    minute: int,
    open_: str,
    high: str,
    low: str,
    close: str,
) -> Candle:
    return Candle(
        symbol="BTCUSDT",
        timeframe=Timeframe.M5,
        open_time=datetime(2026, 8, 15, 10, minute, tzinfo=UTC),
        open=Decimal(open_),
        high=Decimal(high),
        low=Decimal(low),
        close=Decimal(close),
        volume=Decimal("100"),
        quote_volume=Decimal("10000"),
        taker_buy_volume=Decimal("50"),
        trade_count=10,
        source=CandleSource.STREAM,
    )


def make_pool(
    *,
    side: LiquiditySide,
    price: str = "100",
    liquidity_class: LiquidityClass = LiquidityClass.EXTERNAL,
    state: PoolState = PoolState.ACTIVE,
) -> LiquidityPool:
    value = Decimal(price)

    return LiquidityPool(
        pool_id=f"{side.value}-1",
        side=side,
        liquidity_class=liquidity_class,
        source=PoolSource.SWING,
        price=value,
        band_low=value,
        band_high=value,
        created_at=datetime(2026, 8, 15, 9, 0, tzinfo=UTC),
        created_index=0,
        strength=PoolStrength(
            touches_component=Decimal("10"),
            timeframe_component=Decimal("10"),
            age_component=Decimal("10"),
            cluster_component=Decimal("6.25"),
        ),
        state=state,
    )


def make_ssl_sweep(*, reclaimed: bool = False) -> SweepEvent:
    """An SSL sweep of the level-100 pool by a 96-low rejection candle."""

    sweep = detect_single_candle_sweep(
        make_candle(minute=0, open_="101", high="102", low="96", close="101"),
        make_pool(side=LiquiditySide.SSL),
        candle_index=10,
        atr=ATR,
        epsilon=EPSILON,
    )

    assert sweep is not None
    return replace(sweep, reclaimed=reclaimed)


# --------------------------------------------------------------------------
# Single-candle sweep — SSL doctrine and non-detection branches
# --------------------------------------------------------------------------


def test_single_candle_ssl_sweep_measures_depth_below_the_level() -> None:
    event = detect_single_candle_sweep(
        make_candle(minute=0, open_="101", high="102", low="98", close="101"),
        make_pool(side=LiquiditySide.SSL),
        candle_index=10,
        atr=ATR,
        epsilon=EPSILON,
    )

    assert event is not None
    assert event.side is LiquiditySide.SSL
    assert event.penetration_price == Decimal("98")
    assert event.reference_level == LEVEL
    assert event.sweep_depth_atr == Decimal("1")
    assert event.confirmation_window == 1
    assert event.gap_sweep is False


def test_single_candle_ssl_gap_sweep_flags_an_open_below_the_level() -> None:
    event = detect_single_candle_sweep(
        make_candle(minute=0, open_="99", high="101.5", low="98", close="101"),
        make_pool(side=LiquiditySide.SSL),
        candle_index=10,
        atr=ATR,
        epsilon=EPSILON,
    )

    assert event is not None
    assert event.gap_sweep is True


def test_single_candle_bsl_needs_penetration_beyond_epsilon() -> None:
    """A high inside the epsilon band is noise, not a sweep."""

    event = detect_single_candle_sweep(
        make_candle(minute=0, open_="99", high="100.05", low="98", close="99"),
        make_pool(side=LiquiditySide.BSL),
        candle_index=10,
        atr=ATR,
        epsilon=EPSILON,
    )

    assert event is None


def test_single_candle_ssl_needs_penetration_beyond_epsilon() -> None:
    event = detect_single_candle_sweep(
        make_candle(minute=0, open_="101", high="102", low="99.95", close="101"),
        make_pool(side=LiquiditySide.SSL),
        candle_index=10,
        atr=ATR,
        epsilon=EPSILON,
    )

    assert event is None


def test_single_candle_ssl_close_through_is_a_break_not_a_sweep() -> None:
    """Closing below the level is acceptance, which is a break by doctrine."""

    event = detect_single_candle_sweep(
        make_candle(minute=0, open_="101", high="102", low="96", close="97"),
        make_pool(side=LiquiditySide.SSL),
        candle_index=10,
        atr=ATR,
        epsilon=EPSILON,
    )

    assert event is None


# --------------------------------------------------------------------------
# Two-candle sweep
# --------------------------------------------------------------------------


def test_two_candle_ssl_sweep_confirms_on_next_candle_reclaim() -> None:
    event = detect_two_candle_sweep(
        make_candle(minute=0, open_="100.5", high="100.6", low="98", close="99.95"),
        make_candle(minute=5, open_="100", high="101.5", low="99.5", close="101"),
        make_pool(side=LiquiditySide.SSL),
        confirmation_index=11,
        atr=ATR,
        epsilon=EPSILON,
    )

    assert event is not None
    assert event.confirmation_window == 2
    assert event.penetration_price == Decimal("98")
    assert event.close_back_price == Decimal("101")
    assert event.sweep_depth_atr == Decimal("1")


def test_two_candle_bsl_rejects_penetration_inside_epsilon() -> None:
    event = detect_two_candle_sweep(
        make_candle(minute=0, open_="99", high="100.05", low="98", close="100.02"),
        make_candle(minute=5, open_="100", high="100.5", low="98", close="99"),
        make_pool(side=LiquiditySide.BSL),
        confirmation_index=11,
        atr=ATR,
        epsilon=EPSILON,
    )

    assert event is None


def test_two_candle_bsl_rejects_close_outside_the_marginal_band() -> None:
    """The penetration candle must close *just* beyond the level, not below it."""

    event = detect_two_candle_sweep(
        make_candle(minute=0, open_="99", high="102", low="98", close="99"),
        make_candle(minute=5, open_="100", high="100.5", low="98", close="99"),
        make_pool(side=LiquiditySide.BSL),
        confirmation_index=11,
        atr=ATR,
        epsilon=EPSILON,
    )

    assert event is None


def test_two_candle_bsl_rejects_when_confirmation_fails_to_close_back() -> None:
    event = detect_two_candle_sweep(
        make_candle(minute=0, open_="99", high="102", low="98", close="100.05"),
        make_candle(minute=5, open_="100", high="101.5", low="99.5", close="101"),
        make_pool(side=LiquiditySide.BSL),
        confirmation_index=11,
        atr=ATR,
        epsilon=EPSILON,
    )

    assert event is None


def test_two_candle_ssl_rejects_penetration_inside_epsilon() -> None:
    event = detect_two_candle_sweep(
        make_candle(minute=0, open_="101", high="102", low="99.95", close="99.97"),
        make_candle(minute=5, open_="100", high="101.5", low="99.5", close="101"),
        make_pool(side=LiquiditySide.SSL),
        confirmation_index=11,
        atr=ATR,
        epsilon=EPSILON,
    )

    assert event is None


def test_two_candle_ssl_rejects_close_outside_the_marginal_band() -> None:
    event = detect_two_candle_sweep(
        make_candle(minute=0, open_="101", high="102", low="98", close="101"),
        make_candle(minute=5, open_="100", high="101.5", low="99.5", close="101"),
        make_pool(side=LiquiditySide.SSL),
        confirmation_index=11,
        atr=ATR,
        epsilon=EPSILON,
    )

    assert event is None


def test_two_candle_ssl_rejects_when_confirmation_fails_to_close_back() -> None:
    event = detect_two_candle_sweep(
        make_candle(minute=0, open_="100.5", high="100.6", low="98", close="99.95"),
        make_candle(minute=5, open_="100", high="100.2", low="98", close="99"),
        make_pool(side=LiquiditySide.SSL),
        confirmation_index=11,
        atr=ATR,
        epsilon=EPSILON,
    )

    assert event is None


def test_two_candle_ssl_gap_sweep_flags_an_open_below_the_level() -> None:
    event = detect_two_candle_sweep(
        make_candle(minute=0, open_="99.92", high="100", low="98", close="99.95"),
        make_candle(minute=5, open_="100", high="101.5", low="99.5", close="101"),
        make_pool(side=LiquiditySide.SSL),
        confirmation_index=11,
        atr=ATR,
        epsilon=EPSILON,
    )

    assert event is not None
    assert event.gap_sweep is True


# --------------------------------------------------------------------------
# Detector input guards
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "state",
    [PoolState.SWEPT, PoolState.BROKEN, PoolState.EXPIRED],
)
def test_sweep_detector_refuses_a_non_active_pool(state: PoolState) -> None:
    with pytest.raises(ValueError, match="ACTIVE pool"):
        detect_single_candle_sweep(
            make_candle(minute=0, open_="99", high="102", low="98", close="99"),
            make_pool(side=LiquiditySide.BSL, state=state),
            candle_index=10,
            atr=ATR,
            epsilon=EPSILON,
        )


@pytest.mark.parametrize("atr", [Decimal("0"), Decimal("-1")])
def test_sweep_detector_requires_positive_atr(atr: Decimal) -> None:
    with pytest.raises(ValueError, match="atr must be positive"):
        detect_single_candle_sweep(
            make_candle(minute=0, open_="99", high="102", low="98", close="99"),
            make_pool(side=LiquiditySide.BSL),
            candle_index=10,
            atr=atr,
            epsilon=EPSILON,
        )


def test_sweep_detector_requires_non_negative_epsilon() -> None:
    with pytest.raises(ValueError, match="epsilon must be non-negative"):
        detect_two_candle_sweep(
            make_candle(minute=0, open_="99", high="102", low="98", close="100.05"),
            make_candle(minute=5, open_="100", high="100.5", low="98", close="99"),
            make_pool(side=LiquiditySide.BSL),
            confirmation_index=11,
            atr=ATR,
            epsilon=Decimal("-0.01"),
        )


# --------------------------------------------------------------------------
# sweep_reclaimed — the 15-candle relevance window
# --------------------------------------------------------------------------


def test_ssl_sweep_is_reclaimed_by_a_close_back_below_the_level() -> None:
    sweep = make_ssl_sweep()

    updated = sweep_reclaimed(
        sweep,
        make_candle(minute=5, open_="100", high="101", low="98", close="99"),
        candle_index=11,
    )

    assert updated.reclaimed is True


def test_reclaim_is_idempotent_once_set() -> None:
    sweep = make_ssl_sweep(reclaimed=True)

    updated = sweep_reclaimed(
        sweep,
        make_candle(minute=5, open_="100", high="101", low="98", close="99"),
        candle_index=11,
    )

    assert updated is sweep


def test_reclaim_ignores_candles_at_or_before_confirmation() -> None:
    sweep = make_ssl_sweep()

    updated = sweep_reclaimed(
        sweep,
        make_candle(minute=5, open_="100", high="101", low="98", close="99"),
        candle_index=sweep.confirmed_index,
    )

    assert updated is sweep
    assert updated.reclaimed is False


def test_reclaim_expires_after_the_fifteen_candle_window() -> None:
    sweep = make_ssl_sweep()

    assert sweep.setup_expiry_index == sweep.confirmed_index + 15

    updated = sweep_reclaimed(
        sweep,
        make_candle(minute=5, open_="100", high="101", low="98", close="99"),
        candle_index=sweep.setup_expiry_index + 1,
    )

    assert updated.reclaimed is False


def test_reclaim_requires_the_close_to_cross_back() -> None:
    sweep = make_ssl_sweep()

    updated = sweep_reclaimed(
        sweep,
        make_candle(minute=5, open_="100", high="102", low="100", close="101"),
        candle_index=11,
    )

    assert updated is sweep
    assert updated.reclaimed is False


# --------------------------------------------------------------------------
# mark_displaced_after — displacement within three candles
# --------------------------------------------------------------------------


@pytest.mark.parametrize("elapsed", [1, 2, 3])
def test_displacement_within_three_candles_is_recorded(elapsed: int) -> None:
    sweep = make_ssl_sweep()

    updated = mark_displaced_after(
        sweep,
        candle_index=sweep.confirmed_index + elapsed,
        displacement_in_reversal_direction=True,
    )

    assert updated.displaced_after is True


@pytest.mark.parametrize("elapsed", [0, 4, 10])
def test_displacement_outside_the_three_candle_window_is_ignored(elapsed: int) -> None:
    sweep = make_ssl_sweep()

    updated = mark_displaced_after(
        sweep,
        candle_index=sweep.confirmed_index + elapsed,
        displacement_in_reversal_direction=True,
    )

    assert updated is sweep
    assert updated.displaced_after is False


def test_displacement_against_the_reversal_direction_is_ignored() -> None:
    sweep = make_ssl_sweep()

    updated = mark_displaced_after(
        sweep,
        candle_index=sweep.confirmed_index + 1,
        displacement_in_reversal_direction=False,
    )

    assert updated is sweep


def test_displacement_flag_is_idempotent_once_set() -> None:
    sweep = replace(make_ssl_sweep(), displaced_after=True)

    updated = mark_displaced_after(
        sweep,
        candle_index=sweep.confirmed_index + 1,
        displacement_in_reversal_direction=True,
    )

    assert updated is sweep


# --------------------------------------------------------------------------
# Stop hunts — SSL doctrine and every rejection branch
# --------------------------------------------------------------------------


def _detect_ssl_hunt(
    *,
    direction: str = "UP",
    displacement_close: str = "101",
    displacement_index: int = 12,
    sweep_high: str = "102",
    sweep_low: str = "96",
    reclaimed: bool = False,
):
    return detect_stop_hunt(
        make_ssl_sweep(reclaimed=reclaimed),
        displacement_id="disp-1",
        displacement_at=datetime(2026, 8, 15, 10, 10, tzinfo=UTC),
        displacement_index=displacement_index,
        displacement_direction=direction,
        displacement_close=Decimal(displacement_close),
        sweep_candle_high=Decimal(sweep_high),
        sweep_candle_low=Decimal(sweep_low),
    )


def test_external_ssl_stop_hunt_confirms_on_half_range_reclaim() -> None:
    event = _detect_ssl_hunt()

    assert event is not None
    assert event.elapsed_candles == 2
    assert event.failed is False


def test_reclaimed_sweep_cannot_become_a_stop_hunt() -> None:
    assert _detect_ssl_hunt(reclaimed=True) is None


@pytest.mark.parametrize("displacement_index", [10, 14])
def test_stop_hunt_requires_displacement_within_three_candles(
    displacement_index: int,
) -> None:
    assert _detect_ssl_hunt(displacement_index=displacement_index) is None


def test_stop_hunt_rejects_an_inverted_sweep_candle() -> None:
    with pytest.raises(ValueError, match="high cannot be below low"):
        _detect_ssl_hunt(sweep_high="96", sweep_low="102")


def test_stop_hunt_rejects_a_zero_range_sweep_candle() -> None:
    assert _detect_ssl_hunt(sweep_high="100", sweep_low="100") is None


def test_ssl_stop_hunt_requires_upward_displacement() -> None:
    assert _detect_ssl_hunt(direction="DOWN") is None


def test_ssl_stop_hunt_requires_a_close_above_the_midpoint() -> None:
    """Midpoint of the 96-102 sweep candle is 99; a 98 close is not a reclaim."""

    assert _detect_ssl_hunt(displacement_close="98") is None


def test_bsl_stop_hunt_requires_a_close_below_the_midpoint() -> None:
    sweep = detect_single_candle_sweep(
        make_candle(minute=0, open_="99", high="104", low="96", close="99"),
        make_pool(side=LiquiditySide.BSL),
        candle_index=10,
        atr=ATR,
        epsilon=EPSILON,
    )

    assert sweep is not None

    event = detect_stop_hunt(
        sweep,
        displacement_id="disp-1",
        displacement_at=datetime(2026, 8, 15, 10, 10, tzinfo=UTC),
        displacement_index=12,
        displacement_direction="DOWN",
        displacement_close=Decimal("101"),
        sweep_candle_high=Decimal("104"),
        sweep_candle_low=Decimal("96"),
    )

    assert event is None


def test_ssl_stop_hunt_fails_when_price_loses_the_sweep_extreme() -> None:
    event = _detect_ssl_hunt()
    assert event is not None

    failed = mark_stop_hunt_failed(
        event,
        make_ssl_sweep(),
        candle_index=event.confirmed_index + 2,
        candle_close=Decimal("95"),
        sweep_extreme=Decimal("96"),
    )

    assert failed.failed is True


def test_stop_hunt_failure_flag_is_idempotent() -> None:
    event = _detect_ssl_hunt()
    assert event is not None

    failed = replace(event, failed=True)

    unchanged = mark_stop_hunt_failed(
        failed,
        make_ssl_sweep(),
        candle_index=failed.confirmed_index + 2,
        candle_close=Decimal("95"),
        sweep_extreme=Decimal("96"),
    )

    assert unchanged is failed


@pytest.mark.parametrize("elapsed", [0, 6])
def test_stop_hunt_failure_only_observed_within_five_candles(elapsed: int) -> None:
    event = _detect_ssl_hunt()
    assert event is not None

    unchanged = mark_stop_hunt_failed(
        event,
        make_ssl_sweep(),
        candle_index=event.confirmed_index + elapsed,
        candle_close=Decimal("95"),
        sweep_extreme=Decimal("96"),
    )

    assert unchanged is event


def test_stop_hunt_holding_above_the_extreme_is_not_a_failure() -> None:
    event = _detect_ssl_hunt()
    assert event is not None

    unchanged = mark_stop_hunt_failed(
        event,
        make_ssl_sweep(),
        candle_index=event.confirmed_index + 2,
        candle_close=Decimal("98"),
        sweep_extreme=Decimal("96"),
    )

    assert unchanged is event
    assert unchanged.failed is False


# --------------------------------------------------------------------------
# Pool strength scoring guards and cluster tiers
# --------------------------------------------------------------------------


VALID_STRENGTH_ARGS = {
    "touches": 3,
    "timeframe_rank": 6,
    "max_timeframe_rank": 6,
    "age_candles": 200,
    "member_count": 3,
}


@pytest.mark.parametrize(
    ("override", "message"),
    [
        ({"touches": -1}, "touches must be non-negative"),
        ({"timeframe_rank": -1}, "timeframe_rank must be non-negative"),
        ({"max_timeframe_rank": 0}, "max_timeframe_rank must be positive"),
        ({"timeframe_rank": 7}, "cannot exceed max_timeframe_rank"),
        ({"age_candles": -1}, "age_candles must be non-negative"),
        ({"member_count": 0}, "member_count must be positive"),
    ],
)
def test_pool_strength_rejects_invalid_inputs(
    override: dict[str, int],
    message: str,
) -> None:
    args = {**VALID_STRENGTH_ARGS, **override}

    with pytest.raises(ValueError, match=message):
        score_pool_strength(**args)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("member_count", "expected"),
    [
        (1, Decimal("6.25")),
        (2, Decimal("12.5")),
        (3, Decimal("25")),
        (9, Decimal("25")),
    ],
)
def test_cluster_component_tiers(member_count: int, expected: Decimal) -> None:
    strength = score_pool_strength(
        touches=3,
        timeframe_rank=6,
        max_timeframe_rank=6,
        age_candles=200,
        member_count=member_count,
    )

    assert strength.cluster_component == expected


def test_touches_and_age_components_saturate_at_their_caps() -> None:
    capped = score_pool_strength(
        touches=99,
        timeframe_rank=6,
        max_timeframe_rank=6,
        age_candles=9999,
        member_count=3,
    )

    assert capped.touches_component == Decimal("25")
    assert capped.age_component == Decimal("25")
    assert capped.total == Decimal("100")


# --------------------------------------------------------------------------
# pool_from_swing
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("kind", "side"),
    [
        (SwingKind.HIGH, LiquiditySide.BSL),
        (SwingKind.LOW, LiquiditySide.SSL),
    ],
)
def test_pool_from_swing_maps_swing_kind_to_liquidity_side(
    kind: SwingKind,
    side: LiquiditySide,
) -> None:
    swing = SwingPoint(
        index=42,
        open_time=datetime(2026, 8, 15, 9, 0, tzinfo=UTC),
        price=Decimal("100"),
        kind=kind,
        strength=SwingStrength.EXTERNAL,
    )

    pool = pool_from_swing(
        swing,
        pool_id="pool-1",
        liquidity_class=LiquidityClass.EXTERNAL,
        touches=1,
        timeframe_rank=3,
        max_timeframe_rank=6,
        age_candles=100,
    )

    assert pool.side is side
    assert pool.source is PoolSource.SWING
    assert pool.state is PoolState.ACTIVE
    assert pool.member_count == 1
    assert pool.band_low == pool.band_high == swing.price
    assert pool.created_index == swing.index
    assert pool.created_at == swing.open_time
    # A single-member pool sits in the lowest cluster tier.
    assert pool.strength.cluster_component == Decimal("6.25")


def test_pool_from_swing_sweep_level_follows_the_side() -> None:
    high_swing = SwingPoint(
        index=1,
        open_time=datetime(2026, 8, 15, 9, 0, tzinfo=UTC),
        price=Decimal("105"),
        kind=SwingKind.HIGH,
        strength=SwingStrength.EXTERNAL,
    )

    pool = pool_from_swing(
        high_swing,
        pool_id="pool-2",
        liquidity_class=LiquidityClass.INTERNAL,
        touches=2,
        timeframe_rank=6,
        max_timeframe_rank=6,
        age_candles=0,
    )

    assert pool.sweep_level == Decimal("105")
    assert pool.strength.age_component == Decimal("0")
