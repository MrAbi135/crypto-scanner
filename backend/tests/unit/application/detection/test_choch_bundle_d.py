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
        leg_from=_T0,
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
        leg_from=_T0,
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
        leg_from=_T0,
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
        leg_from=_T0,
    )
    beyond = _find_choch(
        candle=_candle(30, close="100.6"),
        trend=TrendState.BEARISH,
        external_classified=(_LH,),
        internal_classified=(),
        consumed=set(),
        epsilon=Decimal("0.5"),
        surpassed=set(),
        leg_from=_T0,
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
        leg_from=_T0,
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
        leg_from=_T0,
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
        leg_from=_T0,
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


# ---------- the current leg (§3.6 v1.0.20, P1b) ----------


async def test_the_walk_ignores_a_swing_from_an_earlier_leg(monkeypatch) -> None:
    """Candle 12 dips to 97 (closing back at 101) and candle 15 makes a new high
    at 110: the running extreme is 15, the last swing high before it is 7, so
    the leg starts at candle 12's low. The HL at 8 lies before that, and candle
    20's close at 94 breaks nothing."""
    series = _walk_series("94")
    series[12] = _candle(12, close="101", low="97", high="102")
    series[15] = _candle(15, close="101", low="99", high="110")

    assert await _chochs(monkeypatch, series) == []


async def test_a_dip_without_a_new_extreme_leaves_the_leg_where_it_was(monkeypatch) -> None:
    series = _walk_series("94")
    series[12] = _candle(12, close="101", low="97", high="102")

    assert await _chochs(monkeypatch, series) == [series[20].open_time.isoformat()]


def test_the_trend_start_comes_from_the_turning_choch() -> None:
    """A recorded CHoCH that turned the trend bullish fixes the start at the
    lowest low from its swing to its candle."""
    from scanner.application.detection.structure_shift_replay import _trend_start, _TurningChoch

    series = [
        _candle(i, close="100", low=low, high="101")
        for i, low in enumerate(["99", "97", "95", "96", "98", "99", "99", "99"])
    ]
    position_of = {c.open_time: i for i, c in enumerate(series)}
    turning = _TurningChoch(TrendState.BULLISH, series[4].open_time, series[1].open_time)

    start = _trend_start(
        [c.low for c in series],
        [c.high for c in series],
        position_of,
        turning=turning,
        trend=TrendState.BULLISH,
        candle=series[7],
        current=7,
        external_classified=(),
    )

    assert start == 2


def test_a_choch_toward_the_other_trend_falls_back_to_the_labelled_swings() -> None:
    from scanner.application.detection.structure_shift_replay import _trend_start, _TurningChoch

    series = [_candle(i, close="100", low="99", high="101") for i in range(8)]
    position_of = {c.open_time: i for i, c in enumerate(series)}
    turning = _TurningChoch(TrendState.BEARISH, series[4].open_time, series[1].open_time)
    hls = tuple(
        ClassifiedSwing(
            swing=SwingPoint(
                i, series[i].open_time, Decimal("99"), SwingKind.LOW, SwingStrength.EXTERNAL
            ),
            label=StructureLabel.HL,
        )
        for i in (3, 5)
    )

    start = _trend_start(
        [c.low for c in series],
        [c.high for c in series],
        position_of,
        turning=turning,
        trend=TrendState.BULLISH,
        candle=series[7],
        current=7,
        external_classified=hls,
    )

    assert start == 3


# ---------- the leg across passes: state only the snapshot holds ----------
#
# The window is candles 0..29; the pass resumes after candle 14. Before the
# window (hours -20..-1) the snapshot holds the leg's candles, with a high of
# 120 at hour -5. Inside the window candle 11 dips to 97 and candle 12 makes
# 110, below that old high.
#
# Read with the snapshot's candles, the running extreme is the old 120, the
# leg reaches back before the window, and the HL at 8 is inside it: candle
# 20's close at 94 is a CHoCH. Read from the window alone, the extreme is
# candle 12, the leg starts at candle 11's low, and the HL at 8 is outside it.


def _leg_window() -> list[Candle]:
    series = _walk_series("94")
    series[11] = _candle(11, close="101", low="97", high="102")
    series[12] = _candle(12, close="101", low="99", high="110")
    return series


def _before_window() -> list[tuple]:
    rows = []
    for hour in range(-20, 0):
        high = Decimal("120") if hour == -5 else Decimal("102")
        rows.append((_T0 + timedelta(hours=hour), Decimal("99"), high))
    return rows


async def _resumed_chochs(monkeypatch, *, leg, turning) -> list[str]:
    from tests.unit.application.detection.test_structure_shift_replay import _service

    import scanner.application.detection.structure_shift_replay as shift_module
    from scanner.application.detection.state import StructureEngineState

    series = _leg_window()
    swings = _bullish_swings(series)
    monkeypatch.setattr(shift_module, "detect_external_swings", lambda _: swings)
    monkeypatch.setattr(shift_module, "detect_internal_swings", lambda _: ())
    service, events = _service(series, ())
    detail = shift_module._snapshot(
        series[:15],
        consumed_choch=set(),
        surpassed=set(),
        leg=leg,
        turning=turning,
        history=[*_before_window(), *((c.open_time, c.low, c.high) for c in series[:15])],
        swings=swings,
        candidate=None,
        mss_watch=None,
        floor_index=-1,
        trend_path=[(series[0].open_time, TrendState.BULLISH)],
    )
    await service._state.save(
        StructureEngineState(
            symbol="BTCUSDT",
            timeframe=Timeframe.H1.value,
            algo_version=service._algo_version,
            last_processed_open_time=series[14].open_time.isoformat(),
            trend_state="BULLISH",
            detail=detail,
        )
    )
    await service.run("BTCUSDT", Timeframe.H1, series[0].open_time, series[-1].close_time)
    return sorted(
        e.event_at.isoformat() for e in events.events.values() if e.event_type.startswith("CHOCH")
    )


async def test_a_resumed_leg_reads_its_start_and_candles_from_before_the_window(
    monkeypatch,
) -> None:
    from scanner.application.detection.structure_shift_replay import _LegTrend

    leg = _LegTrend(TrendState.BULLISH, _T0 + timedelta(hours=-20))

    found = await _resumed_chochs(monkeypatch, leg=leg, turning=None)

    assert found == [(_T0 + timedelta(hours=20)).isoformat()]


async def test_a_resumed_last_choch_sets_the_start_of_a_trend_seen_after_the_resume(
    monkeypatch,
) -> None:
    """No leg in the snapshot: the first evaluation fixes the start from the last
    CHoCH, which turned the trend bullish before the window."""
    from scanner.application.detection.structure_shift_replay import _TurningChoch

    turning = _TurningChoch(
        TrendState.BULLISH, _T0 + timedelta(hours=-8), _T0 + timedelta(hours=-15)
    )

    found = await _resumed_chochs(monkeypatch, leg=None, turning=turning)

    assert found == [(_T0 + timedelta(hours=20)).isoformat()]


async def test_a_leg_from_the_other_trend_is_replaced_not_kept(monkeypatch) -> None:
    """A BEARISH leg started at candle 12; the trend is BULLISH now, so the start
    is fixed again (from the last CHoCH), not read from the old leg."""
    from scanner.application.detection.structure_shift_replay import _LegTrend, _TurningChoch

    leg = _LegTrend(TrendState.BEARISH, _T0 + timedelta(hours=12))
    turning = _TurningChoch(
        TrendState.BULLISH, _T0 + timedelta(hours=-8), _T0 + timedelta(hours=-15)
    )

    found = await _resumed_chochs(monkeypatch, leg=leg, turning=turning)

    assert found == [(_T0 + timedelta(hours=20)).isoformat()]


async def test_without_the_old_candles_the_leg_is_read_from_the_window(monkeypatch) -> None:
    """The negative case: a leg starting inside the window never sees the old
    high, and the HL at 8 lies before the leg."""
    from scanner.application.detection.structure_shift_replay import _LegTrend

    leg = _LegTrend(TrendState.BULLISH, _T0 + timedelta(hours=5))

    assert await _resumed_chochs(monkeypatch, leg=leg, turning=None) == []
