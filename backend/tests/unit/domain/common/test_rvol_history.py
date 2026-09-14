"""RVOL's baseline is history, not the detection window (audit M8, owner ruling 2026-09-14).

§2.11 measures an intraday candle against the same slot over the prior 20 days.
The engines read a 500-candle window, which holds twenty prior days for the
newest ~21 H1 candles and for no M15 or M5 candle, so every reading built on
RVOL -- §6's volume facts, §7.1's participation component -- depended on where
the window started.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from scanner.domain.common import Candle
from scanner.domain.common.rvol import baseline_span, relative_volume, relative_volumes
from scanner.domain.momentum.phases import momentum_phase
from scanner.domain.momentum.score import momentum_score
from scanner.domain.volume.detectors import (
    cross_validate_abnormal_volume,
    detect_contraction,
    detect_expansion,
    detect_volume_spike,
)
from scanner.shared import Timeframe
from tests.support.builders import make_candle

T0 = datetime(2026, 3, 1, tzinfo=UTC)
HOURS_PER_DAY = 24


def hourly(count: int) -> list[Candle]:
    # Volumes vary by candle, so a median over the wrong days cannot agree by luck.
    return [
        make_candle(
            timeframe=Timeframe.H1,
            open_time=T0 + Timeframe.H1.duration * index,
            volume=Decimal(10 + (index * 7919) % 23),
        )
        for index in range(count)
    ]


def history_before(series: list[Candle], start: int) -> list[tuple[datetime, Decimal]]:
    first = series[start].open_time

    return [
        (candle.open_time, candle.volume)
        for candle in series[:start]
        if candle.open_time >= first - baseline_span(Timeframe.H1)
    ]


def test_without_history_every_value_is_the_window_only_reading() -> None:
    series = hourly(25 * HOURS_PER_DAY)

    assert relative_volumes(series) == tuple(
        relative_volume(series, index) for index in range(len(series))
    )


def test_a_candle_reads_the_same_rvol_wherever_the_window_starts() -> None:
    series = hourly(40 * HOURS_PER_DAY)
    reference = relative_volumes(series)
    first_complete = 20 * HOURS_PER_DAY

    for start in (first_complete, first_complete + 5, 30 * HOURS_PER_DAY + 13):
        window = series[start : start + 100]

        # The premise: 100 H1 candles hold no twenty prior days of their own.
        assert set(relative_volumes(window)) == {None}

        values = relative_volumes(window, history_before(series, start))

        assert None not in values
        assert values == reference[start : start + 100]


def test_momentum_participation_reads_the_baseline_before_the_window() -> None:
    series = hourly(21 * HOURS_PER_DAY)
    start = len(series) - 60
    window = series[start:]

    with_history = momentum_score(
        window, 59, rvols=relative_volumes(window, history_before(series, start))
    )
    window_only = momentum_score(window, 59)

    assert with_history is not None
    assert window_only is not None
    assert window_only.participation_component == 0
    assert with_history.participation_component > 0


def test_the_momentum_phase_reads_the_supplied_baseline() -> None:
    # Every candle rises, so the score is 50 without participation and 75 with
    # it. RVOL known only for the newest ten: now = 75, three candles ago = 50.
    window = hourly(60)
    rvols = [None] * 50 + [Decimal(2)] * 10

    phase = momentum_phase(window, 59, rvols=rvols)
    window_only = momentum_phase(window, 59)

    assert phase is not None
    assert window_only is not None
    assert phase.accelerating
    assert not window_only.accelerating


def test_each_volume_detector_reads_the_supplied_baseline() -> None:
    window = [
        make_candle(
            timeframe=Timeframe.H1,
            open_time=T0 + Timeframe.H1.duration * index,
            volume=Decimal(10 + index),
            quote_volume=Decimal(1_000_000),
        )
        for index in range(10)
    ]
    last = len(window) - 1

    def flat(value: str) -> list[Decimal | None]:
        return [Decimal(value)] * len(window)

    # The premise: ten H1 candles hold no baseline, so nothing fires on them.
    assert detect_volume_spike(window, last) is None
    assert detect_expansion(window, last, atrs=flat("1")) is None
    assert not detect_contraction(window, last, atrs=flat("10"))
    assert cross_validate_abnormal_volume(window, last) is None

    assert detect_volume_spike(window, last, rvols=flat("3")) is not None
    assert detect_expansion(window, last, atrs=flat("1"), rvols=flat("2")) is not None
    assert detect_contraction(window, last, atrs=flat("10"), rvols=flat("0.5"))
    assert cross_validate_abnormal_volume(window, last, rvols=flat("5")) is not None
