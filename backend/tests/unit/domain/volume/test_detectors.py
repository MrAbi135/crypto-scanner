"""Volume spike and expansion / contraction against SLS §6.2 and §6.3."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from tests.support.builders import make_candle

from scanner.domain.volume import (
    SPIKE_FLOOR_QUOTE,
    delta_pct,
    detect_contraction,
    detect_expansion,
    detect_volume_spike,
)
from scanner.shared import Timeframe

BASE = datetime(2026, 1, 1, tzinfo=UTC)
BIG = Decimal("1000000")  # comfortably over the $250k floor


def candle(
    *,
    index: int,
    volume: str = "10",
    quote: Decimal = BIG,
    open_: str = "100",
    close: str = "101",
    high: str | None = None,
    low: str | None = None,
    taker_buy: str | None = None,
):
    vol = Decimal(volume)

    return make_candle(
        timeframe=Timeframe.H4,
        open_time=BASE + Timeframe.H4.duration * index,
        open_=Decimal(open_),
        close=Decimal(close),
        high=Decimal(high) if high else None,
        low=Decimal(low) if low else None,
        volume=vol,
        quote_volume=quote,
        # Balanced tape unless a test says otherwise, so delta is exactly zero
        # and cannot accidentally satisfy the conviction threshold.
        taker_buy_volume=Decimal(taker_buy) if taker_buy is not None else vol / 2,
    )


def flat_then(final, *, count: int = 20, volume: str = "10"):
    """`count` identical candles so the baseline is complete, then `final`."""
    return [candle(index=i, volume=volume) for i in range(count)] + [final]


class TestDeltaPct:
    def test_a_fully_bought_candle_is_plus_one(self) -> None:
        assert delta_pct(candle(index=0, volume="10", taker_buy="10")) == Decimal(1)

    def test_a_fully_sold_candle_is_minus_one(self) -> None:
        assert delta_pct(candle(index=0, volume="10", taker_buy="0")) == Decimal(-1)

    def test_a_balanced_candle_is_zero(self) -> None:
        assert delta_pct(candle(index=0, volume="10", taker_buy="5")) == 0

    def test_a_halted_candle_is_unknown_not_neutral(self) -> None:
        """Zero volume is §1.5.4's business, and 0/0 is not a reading."""
        assert delta_pct(candle(index=0, volume="0", taker_buy="0")) is None


class TestVolumeSpike:
    def test_an_elevated_candle_is_not_a_spike(self) -> None:
        """§6.2 requires SPIKE or ABNORMAL -- 1.5x is ELEVATED and must not fire."""
        series = flat_then(candle(index=20, volume="20"))

        assert detect_volume_spike(series, 20) is None

    def test_three_times_baseline_fires(self) -> None:
        series = flat_then(candle(index=20, volume="30"))

        spike = detect_volume_spike(series, 20)

        assert spike is not None
        assert spike.rvol == Decimal(3)

    def test_the_absolute_quote_floor_blocks_a_micro_cap_spike(self) -> None:
        """§6.2's floor exists so an $8k "spike" cannot score.

        Same 5x multiple as a real one -- the only difference is that nobody
        traded enough money for it to mean anything.
        """
        tiny = candle(index=20, volume="50", quote=Decimal("8000"))

        assert detect_volume_spike(flat_then(tiny), 20) is None

        assert detect_volume_spike(flat_then(candle(index=20, volume="50")), 20) is not None

    def test_the_floor_is_inclusive(self) -> None:
        exact = candle(index=20, volume="50", quote=SPIKE_FLOOR_QUOTE)

        assert detect_volume_spike(flat_then(exact), 20) is not None

    @pytest.mark.parametrize(
        ("open_", "close", "direction"),
        [("100", "105", "UP"), ("105", "100", "DOWN"), ("100", "100", "NEUTRAL")],
    )
    def test_direction_comes_from_the_body(self, open_: str, close: str, direction: str) -> None:
        series = flat_then(candle(index=20, volume="30", open_=open_, close=close))

        spike = detect_volume_spike(series, 20)

        assert spike is not None
        assert spike.direction == direction

    def test_a_spike_on_a_doji_is_flagged_absorption_not_directional(self) -> None:
        """§6.2: high participation with no progress scores neutral.

        Calling it bullish or bearish would invent a direction out of a body of
        zero, which is precisely the subjectivity the doctrine keeps out.
        """
        series = flat_then(candle(index=20, volume="30", open_="100", close="100"))

        spike = detect_volume_spike(series, 20)

        assert spike is not None
        assert spike.absorption_candidate is True
        assert spike.direction == "NEUTRAL"

    def test_conviction_needs_a_one_sided_tape(self) -> None:
        balanced = flat_then(candle(index=20, volume="30", taker_buy="15"))
        one_sided = flat_then(candle(index=20, volume="30", taker_buy="30"))

        assert detect_volume_spike(balanced, 20).conviction is False  # type: ignore[union-attr]
        assert detect_volume_spike(one_sided, 20).conviction is True  # type: ignore[union-attr]

    def test_an_incomplete_baseline_yields_no_spike(self) -> None:
        assert detect_volume_spike([candle(index=i, volume="10") for i in range(5)], 4) is None


class TestExpansion:
    def _rising(self, progress_close: str):
        """Twenty flat candles, then three with rising volume."""
        series = [candle(index=i, volume="10") for i in range(20)]

        series += [
            candle(index=20, volume="14", open_="100", close="100"),
            candle(index=21, volume="16", open_="100", close="100"),
            candle(index=22, volume="18", open_="100", close=progress_close),
        ]

        return series

    def test_rising_volume_with_real_progress_expands(self) -> None:
        assert detect_expansion(self._rising("140"), 22) is True

    def test_rising_volume_without_progress_is_churn(self) -> None:
        """§6.3 requires |Cl[i] - O[i+2]| >= 0.75 x ATR.

        Volume climbing through a sideways grind is participation without a
        move; scoring it as validation would credit a move not happening.
        """
        assert detect_expansion(self._rising("100"), 22) is False

    def test_volume_must_rise_on_all_three(self) -> None:
        series = self._rising("140")
        series[21] = candle(index=21, volume="9", open_="100", close="100")

        assert detect_expansion(series, 22) is False

    def test_too_early_in_the_series_cannot_expand(self) -> None:
        assert detect_expansion([candle(index=0, volume="10")], 0) is False


class TestContraction:
    def test_quiet_volume_and_quiet_range_contracts(self) -> None:
        # Wide-ranging history sets a large ATR, then five tight, thin candles.
        series = [
            candle(index=i, volume="100", open_="100", close="120", high="130", low="90")
            for i in range(20)
        ]

        series += [
            candle(index=20 + i, volume="1", open_="100", close="100", high="101", low="99")
            for i in range(5)
        ]

        assert detect_contraction(series, 24) is True

    def test_thin_volume_alone_is_not_contraction(self) -> None:
        """§6.3 is explicit: "volume-only lulls during grinding trends are not
        contraction". A trend still making range on thin volume is a trend, and
        flagging it as coiling inverts the reading.
        """
        series = [
            candle(index=i, volume="100", open_="100", close="120", high="130", low="90")
            for i in range(20)
        ]

        series += [
            candle(index=20 + i, volume="1", open_="100", close="130", high="135", low="85")
            for i in range(5)
        ]

        assert detect_contraction(series, 24) is False

    def test_too_early_in_the_series_cannot_contract(self) -> None:
        assert detect_contraction([candle(index=0, volume="1")], 0) is False
