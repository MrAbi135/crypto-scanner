"""Acceleration and range phases against SLS §7.2 and §7.3."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from tests.support.builders import make_candle

from scanner.domain.momentum import (
    detect_compression,
    detect_range_expansion,
    momentum_phase,
)
from scanner.shared import Timeframe

BASE = datetime(2026, 1, 1, tzinfo=UTC)


def candle(index: int, *, open_: str, close: str, high: str, low: str, volume: str = "10"):
    return make_candle(
        timeframe=Timeframe.H4,
        open_time=BASE + Timeframe.H4.duration * index,
        open_=Decimal(open_),
        close=Decimal(close),
        high=Decimal(high),
        low=Decimal(low),
        volume=Decimal(volume),
    )


def wide(count: int, *, start: int = 100):
    """Wide-ranging history so ATR is large and later candles can be 'tight'."""
    return [
        candle(i, open_="100", close="120", high=str(start + 30), low=str(start - 30))
        for i in range(count)
    ]


class TestAcceleration:
    def test_no_phase_before_both_scores_exist(self) -> None:
        """The differential needs a score three candles back as well as now."""
        assert momentum_phase(wide(31), 30) is None

    def test_a_building_trend_accelerates(self) -> None:
        # The trend starts at 36, and the reason is arithmetic: begun at 35 it
        # already scores 91.7 by index 41, leaving only 8.3 to gain by 44 --
        # under the +10 threshold. Starting at 36 leaves the look-back window
        # at 6 of 10 trending against 10 of 10 now, which is what a building
        # trend actually looks like.
        series = [candle(i, open_="100", close="100", high="101", low="99") for i in range(36)]

        for i in range(36, 45):
            base = 100 + (i - 35) * 8
            series.append(
                candle(
                    i, open_=str(base), close=str(base + 8), high=str(base + 9), low=str(base - 1)
                )
            )

        phase = momentum_phase(series, 44)

        assert phase is not None
        assert phase.accel > 0
        assert phase.accelerating is True

    def test_a_rise_from_nothing_is_not_acceleration(self) -> None:
        """§7.2 requires score >= 50 as well as a +10 differential.

        A jump from 5 to 20 is a larger differential than 60 to 70 and means
        far less; without the level test the engine would call noise a trend.
        """
        series = [candle(i, open_="100", close="100", high="101", low="99") for i in range(45)]

        phase = momentum_phase(series, 44)

        assert phase is not None
        assert phase.accelerating is False


class TestRangeExpansion:
    def test_three_wide_candles_expand(self) -> None:
        series = [candle(i, open_="100", close="101", high="102", low="99") for i in range(30)]

        for i in range(30, 33):
            series.append(candle(i, open_="100", close="115", high="120", low="95"))

        assert detect_range_expansion(series, 32) is True

    def test_ordinary_candles_do_not(self) -> None:
        series = [candle(i, open_="100", close="101", high="102", low="99") for i in range(33)]

        assert detect_range_expansion(series, 32) is False


class TestCompression:
    def _tight_after_wide(self, drift: int):
        series = wide(25)

        for i in range(25, 32):
            base = 100 + drift * (i - 25)
            series.append(
                candle(
                    i, open_=str(base), close=str(base + 1), high=str(base + 2), low=str(base - 2)
                )
            )

        return series

    def test_seven_tight_candles_in_a_tight_envelope_compress(self) -> None:
        assert detect_compression(self._tight_after_wide(drift=0), 31) is True

    def test_tight_candles_walking_downhill_are_not_a_coil(self) -> None:
        """The envelope test is the one that matters.

        Seven small candles can each pass the per-candle range check while
        covering enormous ground between them. That is a trend, not a coil, and
        only the envelope tells them apart -- which is why §7.3 asks for both.

        The drift has to clear the envelope limit to make the point: ATR here
        is 60, so the limit is 120, and a drift of 12 covers only ~80 -- which
        genuinely *is* a coil at this volatility. 25 covers ~154 and is not.
        """
        series = self._tight_after_wide(drift=25)

        # Each candle still passes the per-candle test, so only the envelope
        # can reject this -- which is precisely what is being verified.
        assert all(c.high - c.low <= Decimal("42") for c in series[-7:])

        assert detect_compression(series, 31) is False

    def test_too_early_in_the_series_cannot_compress(self) -> None:
        assert detect_compression(wide(3), 2) is False
