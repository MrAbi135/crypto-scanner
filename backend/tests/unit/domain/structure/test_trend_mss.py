"""Tests for S4 trend state and MSS doctrine."""

from __future__ import annotations

import pytest

from scanner.domain.structure import (
    BreakDirection,
    MssEvidence,
    TrendState,
    TrendStateMachine,
    evaluate_mss,
    mss_is_low_quality,
)


def test_bullish_choch_enters_caution_without_flipping() -> None:
    machine = TrendStateMachine(TrendState.BULLISH)

    state = machine.apply_choch(BreakDirection.DOWN)

    assert state is TrendState.BULLISH_CAUTION


def test_bearish_choch_enters_caution_without_flipping() -> None:
    machine = TrendStateMachine(TrendState.BEARISH)

    state = machine.apply_choch(BreakDirection.UP)

    assert state is TrendState.BEARISH_CAUTION


def test_mss_down_flips_bullish_caution_to_bearish() -> None:
    machine = TrendStateMachine(TrendState.BULLISH_CAUTION)

    assert machine.apply_mss(BreakDirection.DOWN) is TrendState.BEARISH


def test_mss_up_flips_bearish_caution_to_bullish() -> None:
    machine = TrendStateMachine(TrendState.BEARISH_CAUTION)

    assert machine.apply_mss(BreakDirection.UP) is TrendState.BULLISH


def test_failed_mss_restores_previous_bullish_state() -> None:
    machine = TrendStateMachine(TrendState.BULLISH_CAUTION)

    assert machine.fail_mss_candidate() is TrendState.BULLISH


def test_complete_sweep_based_mss_confirms() -> None:
    decision = evaluate_mss(
        MssEvidence(
            direction=BreakDirection.DOWN,
            has_choch=True,
            has_displacement=True,
            has_external_sweep=True,
            followthrough_candles=3,
        )
    )

    assert decision.confirmed is True
    assert decision.reason == "confirmed"


def test_complete_failure_swing_based_mss_confirms() -> None:
    decision = evaluate_mss(
        MssEvidence(
            direction=BreakDirection.UP,
            has_choch=True,
            has_displacement=True,
            has_failure_swing=True,
            followthrough_candles=5,
        )
    )

    assert decision.confirmed is True


@pytest.mark.parametrize(
    ("evidence", "reason"),
    [
        (
            MssEvidence(
                direction=BreakDirection.UP,
                has_choch=False,
                has_displacement=True,
                has_external_sweep=True,
                followthrough_candles=2,
            ),
            "missing_choch",
        ),
        (
            MssEvidence(
                direction=BreakDirection.UP,
                has_choch=True,
                has_displacement=False,
                has_external_sweep=True,
                followthrough_candles=2,
            ),
            "missing_displacement",
        ),
        (
            MssEvidence(
                direction=BreakDirection.UP,
                has_choch=True,
                has_displacement=True,
                followthrough_candles=2,
            ),
            "missing_origin_evidence",
        ),
        (
            MssEvidence(
                direction=BreakDirection.UP,
                has_choch=True,
                has_displacement=True,
                has_external_sweep=True,
                followthrough_candles=None,
            ),
            "missing_followthrough",
        ),
        (
            MssEvidence(
                direction=BreakDirection.UP,
                has_choch=True,
                has_displacement=True,
                has_external_sweep=True,
                followthrough_candles=6,
            ),
            "followthrough_expired",
        ),
        (
            MssEvidence(
                direction=BreakDirection.UP,
                has_choch=True,
                has_displacement=True,
                has_external_sweep=True,
                followthrough_candles=2,
                spans_degraded_data=True,
            ),
            "degraded_data",
        ),
    ],
)
def test_incomplete_mss_does_not_confirm(
    evidence: MssEvidence,
    reason: str,
) -> None:
    decision = evaluate_mss(evidence)

    assert decision.confirmed is False
    assert decision.reason == reason


def test_non_positive_followthrough_is_rejected() -> None:
    with pytest.raises(
        ValueError,
        match="followthrough_candles must be positive",
    ):
        evaluate_mss(
            MssEvidence(
                direction=BreakDirection.UP,
                has_choch=True,
                has_displacement=True,
                has_external_sweep=True,
                followthrough_candles=0,
            )
        )


def test_mss_reversal_within_ten_candles_is_low_quality() -> None:
    assert mss_is_low_quality(
        closes_back_beyond_pre_mss_extreme=True,
        candles_since_confirmation=10,
    )


def test_mss_reversal_after_window_is_not_low_quality() -> None:
    assert not mss_is_low_quality(
        closes_back_beyond_pre_mss_extreme=True,
        candles_since_confirmation=11,
    )
