"""SLS v1.0.20 §3.6, the Bundle D CHoCH rules, one block per rule.

Each rule has a case where it changes the outcome and a case where it must
not, so a mutation that drops the rule or widens it is caught by one of the two.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from scanner.application.detection.structure_shift_replay import _find_choch
from scanner.domain.common import Candle, CandleSource
from scanner.domain.structure import (
    BreakDirection,
    ClassifiedSwing,
    StructureLabel,
    SwingKind,
    SwingPoint,
    SwingStrength,
    TrendState,
)
from scanner.shared import Timeframe

_T0 = datetime(2026, 8, 1, tzinfo=UTC)


def _candle(index: int, *, close: str, low: str | None = None, high: str | None = None) -> Candle:
    price = Decimal(close)
    return Candle(
        symbol="BTCUSDT",
        timeframe=Timeframe.H1,
        open_time=_T0 + timedelta(hours=index),
        open=price,
        high=Decimal(high) if high is not None else price,
        low=Decimal(low) if low is not None else price,
        close=price,
        volume=Decimal("100"),
        quote_volume=Decimal("10000"),
        taker_buy_volume=Decimal("50"),
        trade_count=10,
        source=CandleSource.BACKFILL,
    )


def _swing(
    index: int,
    price: str,
    kind: SwingKind,
    label: StructureLabel,
    strength: SwingStrength = SwingStrength.EXTERNAL,
) -> ClassifiedSwing:
    return ClassifiedSwing(
        swing=SwingPoint(index, _T0 + timedelta(hours=index), Decimal(price), kind, strength),
        label=label,
    )


_HL = _swing(10, "100", SwingKind.LOW, StructureLabel.HL)
_LH = _swing(10, "100", SwingKind.HIGH, StructureLabel.LH)


# ---------- ε (§3.6 `Cl[i] < L[swing] - ε`) ----------


def test_a_close_inside_epsilon_is_not_a_choch() -> None:
    found = _find_choch(
        candle=_candle(30, close="99.6"),
        trend=TrendState.BULLISH,
        external_classified=(_HL,),
        internal_classified=(),
        consumed=set(),
        epsilon=Decimal("0.5"),
        surpassed=set(),
    )

    assert found is None


def test_a_close_beyond_epsilon_is_a_choch() -> None:
    found = _find_choch(
        candle=_candle(30, close="99.4"),
        trend=TrendState.BULLISH,
        external_classified=(_HL,),
        internal_classified=(),
        consumed=set(),
        epsilon=Decimal("0.5"),
        surpassed=set(),
    )

    assert found == (_HL.swing, BreakDirection.DOWN)


def test_a_close_exactly_epsilon_away_is_not_a_choch() -> None:
    """The inequality is strict, as §3.5's is for BOS."""
    found = _find_choch(
        candle=_candle(30, close="99.5"),
        trend=TrendState.BULLISH,
        external_classified=(_HL,),
        internal_classified=(),
        consumed=set(),
        epsilon=Decimal("0.5"),
        surpassed=set(),
    )

    assert found is None


def test_epsilon_applies_upward_in_a_bearish_trend() -> None:
    inside = _find_choch(
        candle=_candle(30, close="100.4"),
        trend=TrendState.BEARISH,
        external_classified=(_LH,),
        internal_classified=(),
        consumed=set(),
        epsilon=Decimal("0.5"),
        surpassed=set(),
    )
    beyond = _find_choch(
        candle=_candle(30, close="100.6"),
        trend=TrendState.BEARISH,
        external_classified=(_LH,),
        internal_classified=(),
        consumed=set(),
        epsilon=Decimal("0.5"),
        surpassed=set(),
    )

    assert inside is None
    assert beyond == (_LH.swing, BreakDirection.UP)


def test_a_close_inside_epsilon_does_not_fall_back_to_an_older_swing() -> None:
    """The most recent eligible swing is the level; failing ε there is no CHoCH,
    even when an older, higher HL is cleared by a wide margin."""
    older = _swing(5, "105", SwingKind.LOW, StructureLabel.HL)

    found = _find_choch(
        candle=_candle(30, close="99.8"),
        trend=TrendState.BULLISH,
        external_classified=(older, _HL),
        internal_classified=(),
        consumed=set(),
        epsilon=Decimal("0.5"),
        surpassed=set(),
    )

    assert found is None


# ---------- the walk: ε from the window's ATR, at the break candle ----------


def _walk_series(break_close: str, dips: dict[int, str] | None = None) -> list[Candle]:
    """Thirty flat candles (range 3) whose candle 20 closes `break_close`,
    just under the HL at 95 the swings below put in force. `dips` closes
    other candles at the given prices."""
    series = []
    dips = dips or {}
    for index in range(30):
        if index == 20:
            series.append(_candle(index, close=break_close, low=break_close, high="101"))
        elif index in dips:
            series.append(_candle(index, close=dips[index], low=dips[index], high="101"))
        else:
            series.append(_candle(index, close="101", low="99", high="102"))
    return series


def _bullish_swings(series: list[Candle]) -> tuple[SwingPoint, ...]:
    """HH 100 < 110 < 120 and HL 80 < 85 < 95: BULLISH once index 8 confirms."""
    shape = (
        (1, "100", SwingKind.HIGH),
        (2, "80", SwingKind.LOW),
        (4, "110", SwingKind.HIGH),
        (5, "85", SwingKind.LOW),
        (7, "120", SwingKind.HIGH),
        (8, "95", SwingKind.LOW),
    )
    return tuple(
        SwingPoint(index, series[index].open_time, Decimal(price), kind, SwingStrength.EXTERNAL)
        for index, price, kind in shape
    )


async def _chochs(monkeypatch, series: list[Candle]) -> list[str]:
    from tests.unit.application.detection.test_structure_shift_replay import _service

    import scanner.application.detection.structure_shift_replay as shift_module

    monkeypatch.setattr(shift_module, "detect_external_swings", lambda _: _bullish_swings(series))
    monkeypatch.setattr(shift_module, "detect_internal_swings", lambda _: ())
    service, events = _service(series, ())
    await service.run("BTCUSDT", Timeframe.H1, series[0].open_time, series[-1].close_time)
    return sorted(
        event.event_at.isoformat()
        for event in events.events.values()
        if event.event_type.startswith("CHOCH")
    )


async def test_the_walk_applies_epsilon_from_the_window_atr(monkeypatch) -> None:
    """A close 0.1 under the HL, with ATR near 3 (ε near 0.15), is not a CHoCH."""
    assert await _chochs(monkeypatch, _walk_series("94.9")) == []


async def test_the_walk_still_records_a_close_well_beyond_the_level(monkeypatch) -> None:
    series = _walk_series("94")
    assert await _chochs(monkeypatch, series) == [series[20].open_time.isoformat()]


# ---------- surpassed levels (§3.6 v1.0.20) ----------


def _key(item: ClassifiedSwing) -> tuple:
    return (item.swing.open_time, item.swing.kind, item.swing.strength)


def test_a_surpassed_swing_is_never_the_level() -> None:
    found = _find_choch(
        candle=_candle(30, close="90"),
        trend=TrendState.BULLISH,
        external_classified=(_HL,),
        internal_classified=(),
        consumed=set(),
        epsilon=Decimal("0"),
        surpassed={_key(_HL)},
    )

    assert found is None


def test_a_surpassed_swing_leaves_the_next_unsurpassed_one_as_the_level() -> None:
    older = _swing(5, "95", SwingKind.LOW, StructureLabel.HL)

    found = _find_choch(
        candle=_candle(30, close="90"),
        trend=TrendState.BULLISH,
        external_classified=(older, _HL),
        internal_classified=(),
        consumed=set(),
        epsilon=Decimal("0"),
        surpassed={_key(_HL)},
    )

    assert found == (older.swing, BreakDirection.DOWN)


async def test_the_walk_skips_a_level_an_earlier_close_already_passed(monkeypatch) -> None:
    """Candle 15 closes under the HL at 95 but inside ε, so no CHoCH then; the
    level is surpassed, and candle 20's deeper close finds no level to break."""
    assert await _chochs(monkeypatch, _walk_series("90", {15: "94.95"})) == []


async def test_a_close_on_the_confirmation_candle_does_not_surpass(monkeypatch) -> None:
    """The HL at index 8 confirms on candle 13; only closes after that count."""
    series = _walk_series("94", {13: "94.95"})
    assert await _chochs(monkeypatch, series) == [series[20].open_time.isoformat()]


async def test_a_high_is_surpassed_only_by_a_close_above_it(monkeypatch) -> None:
    """Closes below a swing high do not surpass it (only the kind's own side counts)."""
    import scanner.application.detection.structure_shift_replay as shift_module

    series = _walk_series("95")
    swings = _bullish_swings(series)
    surpassed: set = set()

    # Close 95: below every high (wrong side), above the lows 80/85, equal to 95.
    shift_module._mark_surpassed(surpassed, series, 20, swings)
    assert surpassed == set()

    # Close 101: above the high at 100 only.
    shift_module._mark_surpassed(surpassed, series, 21, swings)
    assert surpassed == {(series[1].open_time, SwingKind.HIGH, SwingStrength.EXTERNAL)}


async def _chochs_in_two_passes(monkeypatch, series: list[Candle], split: int) -> list[str]:
    """One pass to candle `split - 1`, then a pass over the whole series that
    resumes from the first pass's snapshot."""
    from tests.unit.application.detection.test_structure_shift_replay import _service

    import scanner.application.detection.structure_shift_replay as shift_module

    monkeypatch.setattr(shift_module, "detect_external_swings", lambda _: _bullish_swings(series))
    monkeypatch.setattr(shift_module, "detect_internal_swings", lambda _: ())
    service, events = _service(series, ())
    await service.run("BTCUSDT", Timeframe.H1, series[0].open_time, series[split].open_time)
    await service.run("BTCUSDT", Timeframe.H1, series[0].open_time, series[-1].close_time)
    return sorted(
        event.event_at.isoformat()
        for event in events.events.values()
        if event.event_type.startswith("CHOCH")
    )


async def test_a_resumed_pass_keeps_the_levels_surpassed_before_it(monkeypatch) -> None:
    assert await _chochs_in_two_passes(monkeypatch, _walk_series("90", {14: "94.95"}), 16) == []


async def test_the_last_close_of_a_pass_is_counted_for_the_next_one(monkeypatch) -> None:
    assert await _chochs_in_two_passes(monkeypatch, _walk_series("90", {15: "94.95"}), 16) == []


async def test_two_passes_still_record_a_break_of_an_unsurpassed_level(monkeypatch) -> None:
    series = _walk_series("94")
    expected = [series[20].open_time.isoformat()]
    assert await _chochs_in_two_passes(monkeypatch, series, 16) == expected
