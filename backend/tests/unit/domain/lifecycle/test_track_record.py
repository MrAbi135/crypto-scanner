"""§12.4's hit rate and PRD FC-10.1's small-sample honesty."""

from __future__ import annotations

from decimal import Decimal

import pytest

from scanner.domain.lifecycle.track_record import (
    MIN_SAMPLE_FOR_INFERENCE,
    GroupStats,
    wilson_interval,
)


def stats(*, successes=0, failures=0, expired=0, invalidated=0) -> GroupStats:
    return GroupStats(
        successes=successes,
        failures=failures,
        expired=expired,
        invalidated=invalidated,
    )


def test_expired_signals_are_counted_and_kept_out_of_the_rate() -> None:
    """§12.4: "Expired states are excluded from hit-rate but reported (a
    scanner that times out constantly has a target-selection problem —
    visible, not hidden)".

    Both numbers travel together, because 80% over 5 rated signals with 40
    expired is a different fact from 80% over 45.
    """
    group = stats(successes=4, failures=1, expired=40)

    assert group.resolved == 45
    assert group.hit_rate.rated == 5
    assert group.hit_rate.rate == Decimal("80.00")


def test_invalidated_signals_are_reported_and_unrated_too() -> None:
    """INVALIDATED_EARLY is pre-touch: the premise broke before the entry was
    reached, so there was no trade to win or lose."""

    group = stats(successes=3, failures=1, invalidated=6)

    assert group.resolved == 10
    assert group.hit_rate.rated == 4


def test_a_group_with_no_rated_outcomes_has_no_rate_at_all() -> None:
    """Not zero.

    Zero is a claim — "this archetype never wins" — from no evidence, and a
    client charting zeroes beside real rates draws a story that is not there.
    """
    group = stats(expired=12)

    assert group.hit_rate.rate is None
    assert group.hit_rate.interval is None
    assert group.hit_rate.label == "n=0 — no rated outcomes yet"


def test_an_unbroken_run_does_not_claim_certainty() -> None:
    """The reason Wilson was chosen over the normal approximation.

    `p ± z·√(p(1-p)/n)` gives 100% ± 0 at nine from nine — a claim of
    certainty from nine observations, on a platform whose constitution is that
    its record is its integrity.
    """
    interval = stats(successes=9).hit_rate.interval

    assert interval is not None
    assert interval.low < Decimal("100")
    assert interval.high == Decimal("100")
    # Roughly [70, 100]; asserted loosely because the point is the width, not
    # a decimal place.
    assert Decimal("65") < interval.low < Decimal("75")


def test_an_unbroken_losing_run_is_equally_uncertain() -> None:
    interval = stats(failures=5).hit_rate.interval

    assert interval is not None
    assert interval.low == Decimal("0")
    assert Decimal("35") < interval.high < Decimal("50")


def test_the_interval_narrows_as_the_sample_grows() -> None:
    """The property that makes an interval worth showing at all."""

    small = wilson_interval(6, 10)
    large = wilson_interval(600, 1000)

    assert large.width < small.width
    # Both bracket the same point estimate.
    assert small.low < Decimal("60") < small.high
    assert large.low < Decimal("60") < large.high


@pytest.mark.parametrize("rated", [1, 14, MIN_SAMPLE_FOR_INFERENCE - 1])
def test_a_small_sample_says_so_in_words(rated: int) -> None:
    """PRD FC-10.1's own phrasing, in the payload rather than in a renderer.

    A rule enforced only in one client is a rule the next client does not have.
    """
    group = stats(successes=rated)

    assert not group.hit_rate.sufficient_for_inference
    assert group.hit_rate.label == f"n={rated} — insufficient for inference"


def test_a_sufficient_sample_drops_the_warning_but_keeps_the_count() -> None:
    group = stats(successes=20, failures=20)

    assert group.hit_rate.sufficient_for_inference
    assert group.hit_rate.label == "n=40"


def test_the_label_is_a_label_and_not_a_gate() -> None:
    """The numbers are still returned below the threshold.

    Withholding them would be its own dishonesty; the interval is what carries
    the truth and the flag only makes sure nobody has to read one to notice.
    """
    group = stats(successes=3, failures=1)

    assert group.hit_rate.rate == Decimal("75.00")
    assert group.hit_rate.interval is not None


def test_rates_are_quantised_to_two_places() -> None:
    """These numbers are the platform's public record.

    Two clients rendering `66.66666666667` differently is a difference in the
    track record as far as a reader is concerned.
    """
    assert stats(successes=2, failures=1).hit_rate.rate == Decimal("66.67")


def test_an_interval_over_nothing_is_refused_rather_than_widened() -> None:
    """[0, 100] would render as "somewhere between never and always" for a
    group with no data — which reads as a measurement."""

    with pytest.raises(ValueError, match="at least one trial"):
        wilson_interval(0, 0)


@pytest.mark.parametrize(("successes", "trials"), [(-1, 5), (6, 5)])
def test_impossible_counts_are_refused(successes: int, trials: int) -> None:
    with pytest.raises(ValueError, match="within"):
        wilson_interval(successes, trials)
