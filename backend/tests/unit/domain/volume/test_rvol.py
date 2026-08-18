"""RVOL and its banding against SLS §2.11 and §6.1.

Expectations derived from the spec, never read off the implementation.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from tests.support.builders import make_candle

from scanner.domain.volume import (
    BASELINE_CANDLES,
    BASELINE_DAYS,
    RvolClass,
    baseline_sample,
    classify,
    median,
    relative_volume,
    uses_seasonal_baseline,
)
from scanner.shared import Timeframe

BASE = datetime(2026, 1, 1, tzinfo=UTC)


def series(volumes, timeframe=Timeframe.H4):
    return [
        make_candle(
            timeframe=timeframe,
            open_time=BASE + timeframe.duration * i,
            volume=Decimal(str(v)),
        )
        for i, v in enumerate(volumes)
    ]


class TestMedian:
    def test_odd_sample_takes_the_middle(self) -> None:
        assert median([Decimal(3), Decimal(1), Decimal(2)]) == Decimal(2)

    def test_even_sample_averages_the_two_middles(self) -> None:
        assert median([Decimal(1), Decimal(2), Decimal(3), Decimal(4)]) == Decimal("2.5")

    def test_an_empty_sample_is_unknown_not_zero(self) -> None:
        # Zero would divide into infinity downstream; None forces the caller to
        # decide, which relative_volume does by declining to classify.
        assert median([]) is None

    def test_a_single_outlier_does_not_drag_it(self) -> None:
        """§2.11 rejects mean baselines for exactly this reason.

        One spike in the sample would lift a mean enough to hide the next
        several; the median barely moves.
        """
        calm = [Decimal(10)] * 19 + [Decimal(10_000)]

        assert median(calm) == Decimal(10)


class TestBaselineSelection:
    @pytest.mark.parametrize("timeframe", [Timeframe.M5, Timeframe.M15, Timeframe.H1])
    def test_intraday_timeframes_are_seasonal(self, timeframe: Timeframe) -> None:
        assert uses_seasonal_baseline(timeframe) is True

    @pytest.mark.parametrize("timeframe", [Timeframe.H4, Timeframe.D1, Timeframe.W1])
    def test_higher_timeframes_are_not(self, timeframe: Timeframe) -> None:
        assert uses_seasonal_baseline(timeframe) is False

    def test_the_baseline_never_includes_the_candle_itself(self) -> None:
        """Otherwise a spike partly explains itself away.

        Include index i in its own baseline and a large volume raises the very
        number it is being divided by, compressing every reading toward 1.
        """
        candles = series(list(range(1, 30)))

        sample = baseline_sample(candles, 25)

        assert candles[25].volume not in sample
        assert len(sample) == BASELINE_CANDLES

    def test_a_seasonal_baseline_takes_the_same_slot_on_prior_days(self) -> None:
        """§2.11's whole point: 03:00 is compared to prior 03:00s.

        Built with 24 H1 candles per day so each slot recurs daily. The subject
        sits at hour 3 on day 21; its baseline must be the twenty earlier
        03:00 candles and nothing else.
        """
        volumes = []

        for _day in range(21):
            for hour in range(24):
                # 500 at 03:00, 10 everywhere else -- so a slot-aware baseline
                # and a rolling one give wildly different answers.
                volumes.append(500 if hour == 3 else 10)

        candles = [
            make_candle(
                timeframe=Timeframe.H1,
                open_time=BASE + timedelta(hours=i),
                volume=Decimal(str(v)),
            )
            for i, v in enumerate(volumes)
        ]

        subject = 21 * 24 - 24 + 3  # hour 3 of the final day

        sample = baseline_sample(candles, subject)

        assert len(sample) == BASELINE_DAYS
        assert set(sample) == {Decimal(500)}


class TestRelativeVolume:
    def test_an_incomplete_baseline_is_unknown_rather_than_normal(self) -> None:
        """Defaulting to 1.0 would report NORMAL forever on a cold context.

        That is the failure shape this project keeps meeting: a value that is
        plausible, wrong, and raises nothing.
        """
        assert relative_volume(series([10] * 5), 4) is None

    def test_it_divides_by_the_median_of_the_prior_window(self) -> None:
        candles = series([10] * 20 + [30])

        assert relative_volume(candles, 20) == Decimal(3)

    def test_a_never_traded_baseline_declines_instead_of_dividing_by_zero(self) -> None:
        candles = series([0] * 20 + [50])

        assert relative_volume(candles, 20) is None


class TestClassification:
    @pytest.mark.parametrize(
        ("rvol", "expected"),
        [
            ("0", RvolClass.NORMAL),
            ("1.49", RvolClass.NORMAL),
            ("1.5", RvolClass.ELEVATED),
            ("2.99", RvolClass.ELEVATED),
            ("3.0", RvolClass.SPIKE),
            ("4.99", RvolClass.SPIKE),
            ("5.0", RvolClass.ABNORMAL),
            ("50", RvolClass.ABNORMAL),
        ],
    )
    def test_the_bands_are_inclusive_at_their_lower_edge(
        self, rvol: str, expected: RvolClass
    ) -> None:
        """Every boundary pinned: 1.5 / 3.0 / 5.0 are P.volume.rvol_bands.

        Off-by-one on a band edge is invisible in output and shifts every
        downstream score by one class.
        """
        assert classify(Decimal(rvol)) is expected

    def test_an_unknown_rvol_has_no_class(self) -> None:
        assert classify(None) is None
