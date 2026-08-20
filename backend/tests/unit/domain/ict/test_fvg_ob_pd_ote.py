"""Doctrine tests for FVG, OB derivatives, PD context, and OTE."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from scanner.domain.common import (
    Candle,
    CandleSource,
)
from scanner.domain.ict import (
    Displacement,
    DisplacementDirection,
    FvgState,
    ImpulseDirection,
    ImpulseLeg,
    PdState,
    ZoneBand,
    ZonePolarity,
    ZoneState,
    advance_fvg,
    advance_ote,
    bracketed_dealing_range,
    create_breaker,
    create_ifvg,
    create_mitigation_block,
    detect_fvg,
    detect_order_block,
    detect_ote,
    evaluate_pd_context,
)
from scanner.domain.ict.order_blocks import (
    OrderBlock,
)
from scanner.shared import Timeframe


def make_candle(
    index: int,
    *,
    open_: str,
    high: str,
    low: str,
    close: str,
) -> Candle:
    base = datetime(
        2026,
        8,
        16,
        0,
        0,
        tzinfo=UTC,
    )

    return Candle(
        symbol="BTCUSDT",
        timeframe=Timeframe.M5,
        open_time=base + timedelta(minutes=index * 5),
        open=Decimal(open_),
        high=Decimal(high),
        low=Decimal(low),
        close=Decimal(close),
        volume=Decimal("100"),
        quote_volume=Decimal("10000"),
        taker_buy_volume=Decimal("50"),
        trade_count=10,
        source=CandleSource.REBUILT,
    )


def displacement(
    *,
    index: int,
    direction: DisplacementDirection,
) -> Displacement:
    return Displacement(
        candle_index=index,
        direction=direction,
        body=Decimal("8"),
        candle_range=Decimal("10"),
        mean_body_20=Decimal("2"),
        atr=Decimal("5"),
        body_multiple=Decimal("4"),
        range_multiple=Decimal("2"),
        close_position=Decimal("0.1"),
    )


def test_bullish_fvg_registers_and_stores_ce() -> None:
    candles = [
        make_candle(
            0,
            open_="96",
            high="100",
            low="95",
            close="99",
        ),
        make_candle(
            1,
            open_="99",
            high="105",
            low="98",
            close="104",
        ),
        make_candle(
            2,
            open_="104",
            high="108",
            low="103",
            close="107",
        ),
    ]

    fvg = detect_fvg(
        candles,
        2,
        atr=Decimal("10"),
        middle_is_displacement=False,
        dealing_range_id="range-1",
    )

    assert fvg is not None
    assert fvg.polarity is ZonePolarity.BULLISH
    assert fvg.band.low == Decimal("100")
    assert fvg.band.high == Decimal("103")
    assert fvg.consequent_encroachment == Decimal("101.5")


def test_subthreshold_fvg_requires_middle_displacement() -> None:
    candles = [
        make_candle(
            0,
            open_="96",
            high="100",
            low="95",
            close="99",
        ),
        make_candle(
            1,
            open_="99",
            high="103",
            low="98",
            close="102",
        ),
        make_candle(
            2,
            open_="102",
            high="104",
            low="101",
            close="103",
        ),
    ]

    assert (
        detect_fvg(
            candles,
            2,
            atr=Decimal("10"),
            middle_is_displacement=False,
        )
        is None
    )

    assert (
        detect_fvg(
            candles,
            2,
            atr=Decimal("10"),
            middle_is_displacement=True,
        )
        is not None
    )


def test_fvg_wick_fill_and_close_through_are_distinct() -> None:
    candles = [
        make_candle(
            0,
            open_="96",
            high="100",
            low="95",
            close="99",
        ),
        make_candle(
            1,
            open_="99",
            high="105",
            low="98",
            close="104",
        ),
        make_candle(
            2,
            open_="104",
            high="108",
            low="103",
            close="107",
        ),
    ]

    fvg = detect_fvg(
        candles,
        2,
        atr=Decimal("10"),
        middle_is_displacement=False,
    )

    assert fvg is not None

    wick_fill = advance_fvg(
        fvg,
        make_candle(
            3,
            open_="104",
            high="105",
            low="99",
            close="101",
        ),
        candle_index=3,
    )

    assert wick_fill.state is FvgState.FILLED

    close_through = advance_fvg(
        fvg,
        make_candle(
            3,
            open_="104",
            high="105",
            low="98",
            close="99",
        ),
        candle_index=3,
    )

    assert close_through.state is FvgState.INVERTED

    ifvg = create_ifvg(
        close_through,
        inversion_index=3,
        inversion_at=datetime(
            2026,
            8,
            16,
            0,
            20,
            tzinfo=UTC,
        ),
    )

    assert ifvg.polarity is ZonePolarity.BEARISH


def test_bullish_order_block_uses_full_and_refined_ranges() -> None:
    candles = [
        make_candle(
            0,
            open_="100",
            high="103",
            low="99",
            close="102",
        ),
        make_candle(
            1,
            open_="105",
            high="106",
            low="100",
            close="101",
        ),
        make_candle(
            2,
            open_="101",
            high="112",
            low="100",
            close="111",
        ),
    ]

    ob = detect_order_block(
        candles,
        candidate_end_index=1,
        displacement=displacement(
            index=2,
            direction=DisplacementDirection.BULLISH,
        ),
        atr=Decimal("10"),
        external_structure_break=True,
        internal_structure_break=False,
        mss_origin=False,
        fvg_created=False,
        origin_swept=True,
        origin_failure_swing=False,
    )

    assert ob is not None
    assert ob.grade == "OB_A"
    assert ob.polarity is ZonePolarity.BULLISH
    assert ob.band == ZoneBand(
        low=Decimal("100"),
        high=Decimal("106"),
    )
    assert ob.refined_band == ZoneBand(
        low=Decimal("101"),
        high=Decimal("105"),
    )


def make_invalidated_bearish_ob(
    *,
    origin_swept: bool,
    origin_failure_swing: bool,
) -> OrderBlock:
    return OrderBlock(
        ob_id="ob-1",
        polarity=ZonePolarity.BEARISH,
        band=ZoneBand(
            low=Decimal("100"),
            high=Decimal("106"),
        ),
        refined_band=ZoneBand(
            low=Decimal("101"),
            high=Decimal("105"),
        ),
        created_index=1,
        confirmed_index=3,
        created_at=datetime(
            2026,
            8,
            16,
            0,
            5,
            tzinfo=UTC,
        ),
        grade="OB_B",
        mss_origin=False,
        origin_swept=origin_swept,
        origin_failure_swing=origin_failure_swing,
        stale_context=False,
        state=ZoneState.INVALIDATED,
    )


def test_origin_sweep_creates_breaker() -> None:
    breaker = create_breaker(
        make_invalidated_bearish_ob(
            origin_swept=True,
            origin_failure_swing=False,
        ),
        invalidation_index=10,
        invalidation_at=datetime(
            2026,
            8,
            16,
            1,
            0,
            tzinfo=UTC,
        ),
        displacement=displacement(
            index=10,
            direction=DisplacementDirection.BULLISH,
        ),
        structure_break=True,
    )

    assert breaker is not None
    assert breaker.grade == "BRK_A"
    assert breaker.polarity is ZonePolarity.BULLISH


def test_failure_swing_without_sweep_creates_mitigation_block() -> None:
    mitigation = create_mitigation_block(
        make_invalidated_bearish_ob(
            origin_swept=False,
            origin_failure_swing=True,
        ),
        invalidation_index=10,
        invalidation_at=datetime(
            2026,
            8,
            16,
            1,
            0,
            tzinfo=UTC,
        ),
        displacement=displacement(
            index=10,
            direction=DisplacementDirection.BULLISH,
        ),
        structure_break=True,
    )

    assert mitigation is not None
    assert mitigation.grade == "MIT"
    assert mitigation.polarity is ZonePolarity.BULLISH


def test_pd_context_reports_discount_and_extreme_third() -> None:
    dealing_range = bracketed_dealing_range(
        range_id="range-1",
        external_low=Decimal("80"),
        external_high=Decimal("120"),
        low_anchor_index=1,
        high_anchor_index=10,
        close=Decimal("90"),
    )

    assert dealing_range is not None

    context = evaluate_pd_context(
        dealing_range,
        close=Decimal("90"),
        atr=Decimal("10"),
    )

    assert context.state is PdState.DISCOUNT
    assert context.range_position == Decimal("0.2500")
    assert context.long_gate is True
    assert context.sweep_long_gate is True
    assert context.short_gate is False


def test_narrow_dealing_range_suspends_pd() -> None:
    dealing_range = bracketed_dealing_range(
        range_id="range-1",
        external_low=Decimal("100"),
        external_high=Decimal("110"),
        low_anchor_index=1,
        high_anchor_index=10,
        close=Decimal("105"),
    )

    assert dealing_range is not None

    context = evaluate_pd_context(
        dealing_range,
        close=Decimal("105"),
        atr=Decimal("10"),
    )

    assert context.state is PdState.SUSPENDED
    assert context.range_position is None


def test_bullish_ote_uses_62_to_79_percent_retracement() -> None:
    leg = ImpulseLeg(
        leg_id="leg-1",
        direction=ImpulseDirection.BULLISH,
        origin_price=Decimal("100"),
        extreme_price=Decimal("120"),
        origin_index=1,
        end_index=10,
        confirmed_at=datetime(
            2026,
            8,
            16,
            1,
            0,
            tzinfo=UTC,
        ),
    )

    ote = detect_ote(
        leg,
        atr=Decimal("5"),
    )

    assert ote is not None
    assert ote.band.low == Decimal("104.20")
    assert ote.band.high == Decimal("107.60")

    dealing_range = bracketed_dealing_range(
        range_id="range-1",
        external_low=Decimal("80"),
        external_high=Decimal("140"),
        low_anchor_index=0,
        high_anchor_index=15,
        close=Decimal("108"),
    )

    assert dealing_range is not None

    pd_context = evaluate_pd_context(
        dealing_range,
        close=Decimal("108"),
        atr=Decimal("10"),
    )

    updated = advance_ote(
        ote,
        make_candle(
            11,
            open_="109",
            high="110",
            low="105",
            close="108",
        ),
        candle_index=11,
        pd_context=pd_context,
        trend_matches=True,
        leg_end_consumed=False,
    )

    assert updated.state is ZoneState.MITIGATED
