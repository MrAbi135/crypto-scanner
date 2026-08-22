"""§6.6 Fake Volume Defense."""

from __future__ import annotations

from decimal import Decimal

import pytest

from scanner.domain.volume import (
    FakeVolumeTests,
    WashRiskState,
    evaluate_wash_risk,
    excess_suspect_candles,
    fake_volume_score,
    round_trip_symmetry,
    tags_wash_risk,
    trade_size_uniformity,
)


class TestComposite:
    def test_one_failed_test_never_tags(self) -> None:
        """§6.6: "no single test may tag a symbol"."""
        tests = FakeVolumeTests(round_trip_symmetry=True, trade_size_uniformity=False)

        assert fake_volume_score(tests) == Decimal(25)
        assert not tags_wash_risk(tests)

    def test_two_failed_tests_reach_the_threshold(self) -> None:
        tests = FakeVolumeTests(round_trip_symmetry=True, excess_suspect_candles=True)

        assert fake_volume_score(tests) == Decimal(50)
        assert tags_wash_risk(tests)

    def test_an_unmeasured_test_scores_nothing(self) -> None:
        """Two of the four need data this build only recently began collecting.

        A symbol whose depth was never sampled has not been shown to have
        honest volume -- nor dishonest.
        """
        tests = FakeVolumeTests(round_trip_symmetry=True)

        assert tests.measured == 1
        assert tests.failed == 1
        assert not tags_wash_risk(tests)

    def test_a_symbol_that_passes_everything_scores_zero(self) -> None:
        tests = FakeVolumeTests(
            volume_unsupported_by_depth=False,
            round_trip_symmetry=False,
            trade_size_uniformity=False,
            excess_suspect_candles=False,
        )

        assert tests.measured == 4
        assert fake_volume_score(tests) == 0
        assert not tags_wash_risk(tests)


class TestRoundTripSymmetry:
    def test_a_perfectly_two_sided_tape_under_volume_trips(self) -> None:
        assert (
            round_trip_symmetry(
                absolute_delta=Decimal(1),
                total_volume=Decimal(1000),
                rvol_elevated=True,
            )
            is True
        )

    def test_the_same_symmetry_on_a_quiet_day_does_not(self) -> None:
        """A two-sided tape on a quiet day is a quiet day. It is the symmetry
        *under elevated volume* that has no honest explanation."""
        assert (
            round_trip_symmetry(
                absolute_delta=Decimal(1),
                total_volume=Decimal(1000),
                rvol_elevated=False,
            )
            is False
        )

    def test_a_one_sided_day_does_not_trip(self) -> None:
        assert (
            round_trip_symmetry(
                absolute_delta=Decimal(500),
                total_volume=Decimal(1000),
                rvol_elevated=True,
            )
            is False
        )

    def test_a_symbol_that_did_not_trade_has_no_reading(self) -> None:
        assert (
            round_trip_symmetry(
                absolute_delta=Decimal(0),
                total_volume=Decimal(0),
                rvol_elevated=True,
            )
            is None
        )

    def test_meme_thresholds_are_wider_so_more_symbols_trip(self) -> None:
        """§1.8 tightens the tests by 20%, and a test that fires *below* a
        threshold is tightened by raising it."""
        args = {
            "absolute_delta": Decimal(22),
            "total_volume": Decimal(1000),
            "rvol_elevated": True,
        }

        assert round_trip_symmetry(**args) is False
        assert round_trip_symmetry(**args, meme=True) is True


class TestTradeSizeUniformity:
    def test_identical_prints_are_the_wash_signature(self) -> None:
        assert (
            trade_size_uniformity(
                mean_trade_size=Decimal(10),
                stddev_trade_size=Decimal(0),
            )
            is True
        )

    def test_a_varied_tape_does_not_trip(self) -> None:
        assert (
            trade_size_uniformity(
                mean_trade_size=Decimal(10),
                stddev_trade_size=Decimal(5),
            )
            is False
        )

    def test_no_prints_is_no_reading(self) -> None:
        assert (
            trade_size_uniformity(
                mean_trade_size=Decimal(0),
                stddev_trade_size=Decimal(0),
            )
            is None
        )


class TestSuspectCandleCount:
    def test_the_threshold_is_more_than_five(self) -> None:
        assert excess_suspect_candles(5) is False
        assert excess_suspect_candles(6) is True

    def test_a_meme_symbol_trips_on_fewer(self) -> None:
        """A count is tightened by lowering it, not by raising it."""
        assert excess_suspect_candles(5, meme=True) is True


class TestHysteresis:
    def test_tagging_is_immediate(self) -> None:
        assert evaluate_wash_risk(WashRiskState(), True) == WashRiskState(True, 0)

    def test_lifting_takes_three_clean_days(self) -> None:
        """§6.6 gives three because the tests it composes can be tripped by
        legitimate high-frequency market-making on any single day."""
        state = WashRiskState(tagged=True)

        state = evaluate_wash_risk(state, False)
        assert state == WashRiskState(True, 1)

        state = evaluate_wash_risk(state, False)
        assert state == WashRiskState(True, 2)

        state = evaluate_wash_risk(state, False)
        assert state == WashRiskState(False, 0)

    def test_one_dirty_day_restarts_the_count(self) -> None:
        state = evaluate_wash_risk(WashRiskState(tagged=True, clean_days=2), True)

        assert state == WashRiskState(True, 0)

    def test_a_clean_symbol_stays_clean(self) -> None:
        assert evaluate_wash_risk(WashRiskState(), False) == WashRiskState(False, 0)


def test_the_no_single_test_rule_is_asserted_not_restated() -> None:
    """§6.6 states it separately from the arithmetic, so the code checks the
    arithmetic still enforces it."""
    import scanner.domain.volume.fake_volume as module

    original = module.TEST_POINTS
    module.TEST_POINTS = Decimal(50)

    try:
        with pytest.raises(AssertionError, match="single failed test"):
            tags_wash_risk(FakeVolumeTests(round_trip_symmetry=True))
    finally:
        module.TEST_POINTS = original
