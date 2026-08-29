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
    dealing_range_at,
    detect_fvg,
    detect_order_block,
    detect_ote,
    evaluate_pd_context,
)
from scanner.domain.ict.order_blocks import (
    OrderBlock,
)
from scanner.domain.structure import SwingKind, SwingPoint, SwingStrength
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


def make_swing(
    index: int,
    price: str,
    kind: SwingKind,
    *,
    strength: SwingStrength,
) -> SwingPoint:
    return SwingPoint(
        index=index,
        open_time=datetime(2026, 8, 15, tzinfo=UTC) + Timeframe.H1.duration * index,
        price=Decimal(price),
        kind=kind,
        strength=strength,
    )


def test_dealing_range_consumes_only_confirmed_swings() -> None:
    """§3.1: "no downstream logic may consume it earlier" — a swing exists at
    the close of its k-th follow-up candle, not at its pivot.

    Filtering on the pivot alone let a replay anchor ranges on swings a live
    engine could not yet see (k_ext = 5 candles of look-ahead), so live and
    replay disagreed about premium/discount on identical data — the §0.2
    non-repaint break §13 calls a critical defect.
    """
    swings = (
        make_swing(10, "80", SwingKind.LOW, strength=SwingStrength.EXTERNAL),
        make_swing(20, "120", SwingKind.HIGH, strength=SwingStrength.EXTERNAL),
        # Pivoted at 30, confirmed only at 35 — a higher high that would
        # re-anchor the range upward if look-ahead were allowed.
        make_swing(30, "140", SwingKind.HIGH, strength=SwingStrength.EXTERNAL),
    )

    at_32 = dealing_range_at(swings, close=Decimal("100"), index=32)
    at_35 = dealing_range_at(swings, close=Decimal("100"), index=35)

    assert at_32 is not None
    assert at_32.high == Decimal("120"), "the unconfirmed 140 must not anchor yet"

    assert at_35 is not None
    assert at_35.high == Decimal("140"), "once confirmed it re-anchors"


def test_pd_gates_are_decided_on_the_unquantised_position() -> None:
    """§0.4: quantisation is a presentation rule, never a decision rule.

    Raw position 0.50004 quantises (ROUND_HALF_EVEN) to 0.5000; a gate read
    off the quantised value opens for a long the raw comparison refuses.
    Range [0, 100000] with close 50004 lands exactly there.
    """
    dealing_range = bracketed_dealing_range(
        range_id="r",
        external_low=Decimal("0"),
        external_high=Decimal("100000"),
        low_anchor_index=1,
        high_anchor_index=10,
        close=Decimal("50004"),
    )

    assert dealing_range is not None

    context = evaluate_pd_context(
        dealing_range,
        close=Decimal("50004"),
        atr=Decimal("10"),
    )

    # The RECORDED position still quantises to the boundary...
    assert context.range_position == Decimal("0.5000")
    # ...and the verdicts come from the raw value anyway.
    assert context.long_gate is False
    assert context.short_gate is True


def test_every_pd_gate_has_its_own_quantisation_boundary_case() -> None:
    """One boundary per gate, because each survives the others' cases: the
    long-gate test at raw 0.50004 says nothing about short_gate (true either
    way there) -- exactly how two of these four mutations outlived the first
    test."""

    def context_at(close: str):
        dealing_range = bracketed_dealing_range(
            range_id="r",
            external_low=Decimal("0"),
            external_high=Decimal("100000"),
            low_anchor_index=1,
            high_anchor_index=10,
            close=Decimal(close),
        )

        assert dealing_range is not None

        return evaluate_pd_context(dealing_range, close=Decimal(close), atr=Decimal("10"))

    # Raw 0.49996 quantises UP to 0.5000: quantised short_gate would open.
    below_half = context_at("49996")
    assert below_half.range_position == Decimal("0.5000")
    assert below_half.short_gate is False
    assert below_half.long_gate is True

    # Raw 0.33004 quantises DOWN to 0.3300: quantised sweep_long would open.
    above_third = context_at("33004")
    assert above_third.range_position == Decimal("0.3300")
    assert above_third.sweep_long_gate is False

    # Raw 0.66996 quantises UP to 0.6700: quantised sweep_short would open.
    below_upper_third = context_at("66996")
    assert below_upper_third.range_position == Decimal("0.6700")
    assert below_upper_third.sweep_short_gate is False
