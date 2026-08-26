"""Every zone identity must survive the window sliding.

The scanner replays a trailing 500-candle window on every close. A zone
detected on one pass sits at index 412; on the next pass the window has moved
one candle and the same zone sits at 411. Any identity built from that offset
is a different identity every pass — a new row, forever.

**This is not hypothetical.** Measured on the staging host on 2026-08-26,
before the fix: BTCUSDT M5 held 281,781 zone rows for 877 distinct
(type, band) pairs, and one order block had 962 rows under 482 distinct
`created_index` values. Worse than the storage, the sixty-zone window SLS §5.1
gives to §8 confluence contained **eight** distinct zones — the scanner was
scoring against a fraction of the context the doctrine specifies.

Determinism over identical inputs, which the tests this replaces checked, is
true of any hash function and cannot fail. What follows detects the same zone
from two different window offsets and asserts one identity.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from scanner.domain.common import Candle, CandleSource
from scanner.domain.ict.bpr import compose_bpr
from scanner.domain.ict.breakers import create_breaker
from scanner.domain.ict.displacement import Displacement, DisplacementDirection
from scanner.domain.ict.fvg import detect_fvg
from scanner.domain.ict.ifvg import create_ifvg
from scanner.domain.ict.mitigation import create_mitigation_block
from scanner.domain.ict.model import FvgState, ZoneState
from scanner.domain.ict.order_blocks import detect_order_block
from scanner.shared import Timeframe

T0 = datetime(2026, 8, 26, tzinfo=UTC)

# Two offsets the same candle occupies on consecutive passes.
EARLY, LATE = 120, 119


def make_candle(index: int, *, open_: str, high: str, low: str, close: str) -> Candle:
    """A candle whose `open_time` follows its position in the market, not the window."""

    return Candle(
        symbol="BTCUSDT",
        timeframe=Timeframe.M5,
        open_time=T0 + timedelta(minutes=5 * index),
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


def displacement(*, index: int, direction: DisplacementDirection) -> Displacement:
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


def gap_candles(start: int) -> list[Candle]:
    """Three candles leaving an unfilled bullish gap, starting at market position `start`."""

    return [
        make_candle(start, open_="96", high="100", low="95", close="99"),
        make_candle(start + 1, open_="99", high="105", low="98", close="104"),
        make_candle(start + 2, open_="104", high="108", low="103", close="107"),
    ]


def padding(count: int, *, before: int) -> list[Candle]:
    """Flat candles that push the gap deeper into the window."""

    return [
        make_candle(before - count + i, open_="96", high="97", low="95", close="96")
        for i in range(count)
    ]


def test_a_gap_detected_at_two_window_offsets_is_one_gap() -> None:
    """The window genuinely slides here — this is not the same call twice.

    The same three candles are detected once at offset 5 and once at offset 2,
    exactly as they would be on two consecutive passes as older candles fall
    off the front of the trailing window.
    """
    gap = gap_candles(50)

    deep = [*padding(3, before=50), *gap]
    shallow = list(gap)

    early = detect_fvg(deep, 5, atr=Decimal("10"), middle_is_displacement=False)
    late = detect_fvg(shallow, 2, atr=Decimal("10"), middle_is_displacement=False)

    assert early is not None
    assert late is not None
    assert early.created_index != late.created_index
    # One gap in the market, one identity.
    assert early.fvg_id == late.fvg_id


def test_two_different_gaps_still_differ() -> None:
    """The other half of the property.

    An id stable against the window is worthless if it is also stable against
    the zone — a constant would satisfy the test above.
    """
    first = detect_fvg(gap_candles(50), 2, atr=Decimal("10"), middle_is_displacement=False)
    second = detect_fvg(gap_candles(90), 2, atr=Decimal("10"), middle_is_displacement=False)

    assert first is not None
    assert second is not None
    assert first.fvg_id != second.fvg_id


def inverted_gap():
    gap = detect_fvg(gap_candles(50), 2, atr=Decimal("10"), middle_is_displacement=False)

    assert gap is not None

    return type(gap)(
        **{f: getattr(gap, f) for f in gap.__slots__ if f != "state"},
        state=FvgState.INVERTED,
    )


def test_an_inverted_gap_keeps_its_identity() -> None:
    parent = inverted_gap()
    at = T0 + timedelta(hours=3)

    early = create_ifvg(parent, inversion_index=EARLY, inversion_at=at)
    late = create_ifvg(parent, inversion_index=LATE, inversion_at=at)

    assert early.ifvg_id == late.ifvg_id


def test_a_balanced_price_range_keeps_its_identity() -> None:
    bull = detect_fvg(gap_candles(50), 2, atr=Decimal("10"), middle_is_displacement=False)

    bear = detect_fvg(
        [
            make_candle(70, open_="108", high="109", low="103", close="104"),
            make_candle(71, open_="104", high="104", low="90", close="91"),
            make_candle(72, open_="91", high="97", low="88", close="89"),
        ],
        2,
        atr=Decimal("10"),
        middle_is_displacement=False,
    )

    assert bull is not None
    assert bear is not None

    at = T0 + timedelta(hours=4)

    # Small offsets: §5's BPR pair-age guard is measured from the gaps'
    # `created_index`, and 120 would age them out before the id is reached.
    early = compose_bpr(bull, bear, current_index=5, created_at=at)
    late = compose_bpr(bull, bear, current_index=4, created_at=at)

    assert early is not None
    assert late is not None
    assert early.bpr_id == late.bpr_id


def order_block_window(start: int) -> list[Candle]:
    """A down-close candle followed by a bullish displacement."""

    return [
        *(make_candle(start + i, open_="100", high="101", low="99", close="100") for i in range(3)),
        make_candle(start + 3, open_="101", high="101", low="98", close="98"),
        make_candle(start + 4, open_="98", high="115", low="98", close="114"),
    ]


def block_at(
    candles: list[Candle],
    *,
    end: int,
    disp: int,
    origin_swept: bool = False,
    origin_failure_swing: bool = False,
):
    return detect_order_block(
        candles,
        candidate_end_index=end,
        displacement=displacement(index=disp, direction=DisplacementDirection.BULLISH),
        atr=Decimal("2"),
        external_structure_break=True,
        internal_structure_break=False,
        mss_origin=False,
        fvg_created=True,
        origin_swept=origin_swept,
        origin_failure_swing=origin_failure_swing,
    )


def test_an_order_block_detected_at_two_offsets_is_one_block() -> None:
    """The same shift as the gap test, on the zone type with two indices in
    its identity — `created_index` *and* `confirmed_index`."""

    window = order_block_window(50)

    deep = [*padding(4, before=50), *window]

    early = block_at(deep, end=7, disp=8)
    late = block_at(list(window), end=3, disp=4)

    assert early is not None
    assert late is not None
    assert early.created_index != late.created_index
    assert early.ob_id == late.ob_id


def invalidated_block(**flags: bool):
    """§5 requires an INVALIDATED parent, and each child a different origin story.

    A breaker needs the origin swept; a mitigation block needs it unswept with
    a failure swing. Both guards are doctrine, not fixture noise.
    """
    block = block_at(order_block_window(50), end=3, disp=4, **flags)

    assert block is not None

    return type(block)(
        **{f: getattr(block, f) for f in block.__slots__ if f != "state"},
        state=ZoneState.INVALIDATED,
    )


def test_a_breaker_keeps_its_identity() -> None:
    parent = invalidated_block(origin_swept=True)
    at = T0 + timedelta(hours=5)

    # The displacement carries its own index and §5 requires it to match the
    # invalidation — so it slides with the window too, which is the point.
    early = create_breaker(
        parent,
        invalidation_index=EARLY,
        invalidation_at=at,
        displacement=displacement(index=EARLY, direction=DisplacementDirection.BEARISH),
        structure_break=True,
    )
    late = create_breaker(
        parent,
        invalidation_index=LATE,
        invalidation_at=at,
        displacement=displacement(index=LATE, direction=DisplacementDirection.BEARISH),
        structure_break=True,
    )

    assert early is not None
    assert late is not None
    assert early.breaker_id == late.breaker_id


def test_a_mitigation_block_keeps_its_identity() -> None:
    parent = invalidated_block(origin_failure_swing=True)
    at = T0 + timedelta(hours=5)

    early = create_mitigation_block(
        parent,
        invalidation_index=EARLY,
        invalidation_at=at,
        displacement=displacement(index=EARLY, direction=DisplacementDirection.BEARISH),
        structure_break=True,
    )
    late = create_mitigation_block(
        parent,
        invalidation_index=LATE,
        invalidation_at=at,
        displacement=displacement(index=LATE, direction=DisplacementDirection.BEARISH),
        structure_break=True,
    )

    assert early is not None
    assert late is not None
    assert early.mitigation_id == late.mitigation_id
