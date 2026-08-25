"""§12.4's hit rate, and PRD FC-10.1's honesty about how much it is worth.

PRD FC-10.1: *"early-version small-sample stats display confidence-interval
honesty (\"n=14 — insufficient for inference\")"*. That is the whole reason
this module is not two divisions inline in an endpoint.

**The interval is Wilson's, not the normal approximation.** The textbook
`p ± z·√(p(1-p)/n)` is exactly wrong where it matters most here: at small `n`
and at extreme `p` it produces bounds outside [0, 1] and collapses to a width
of zero when a run is unbroken. Nine wins out of nine would report a hit rate
of 100% ± 0 — a claim of certainty from nine observations, on a platform whose
constitution is that the record is its integrity. Wilson gives roughly
[70%, 100%] there, which is the honest answer.

**Expired signals are counted and reported, and excluded from the rate.**
§12.4: "a scanner that times out constantly has a target-selection problem —
visible, not hidden". So the denominator is SUCCESS + FAILED while `n_resolved`
carries everything, and both travel together: a hit rate of 80% over 5 rated
signals when 40 expired is a different fact from 80% over 45.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal, localcontext

# 95%, two-sided. A `Decimal` literal, not a float: Constitution §45.8 keeps
# floats out of `domain/` entirely, and the guard in CI is not being pedantic
# here — these bounds are the platform's published record, and binary
# floating point would put a rounding path between the counts and the number
# a reader sees.
#
# `math.sqrt` is unavailable for the same reason, so the interval is computed
# with `Decimal.sqrt` under a widened context; see `wilson_interval`.
Z_95 = Decimal("1.959963984540054")

CONFIDENCE_LEVEL = "95%"

# Below this, the endpoint says so in words as well as in the interval.
#
# Thirty is the conventional line for a proportion estimate, and PRD FC-10.1's
# own example ("n=14") sits below it. It is a label, not a gate: the numbers
# are still returned, because withholding them would be its own dishonesty —
# the interval is what carries the truth, and the flag only makes sure nobody
# has to read an interval to notice.
MIN_SAMPLE_FOR_INFERENCE = 30

_PERCENT = Decimal("0.01")


@dataclass(frozen=True, slots=True)
class Interval:
    low: Decimal
    high: Decimal

    @property
    def width(self) -> Decimal:
        return self.high - self.low


@dataclass(frozen=True, slots=True)
class HitRate:
    """A rate, its interval, and how much of it there is.

    `rate` is None when nothing was rated. Zero would be a claim — "this
    archetype never wins" — from no evidence at all, and a client charting
    zeroes beside real rates would draw a story that is not there.
    """

    successes: int
    failures: int
    rated: int
    rate: Decimal | None
    interval: Interval | None

    @property
    def sufficient_for_inference(self) -> bool:
        return self.rated >= MIN_SAMPLE_FOR_INFERENCE

    @property
    def label(self) -> str:
        """PRD FC-10.1's phrasing, so the honesty is in the payload itself.

        Built here rather than left to each client: a rule enforced only in a
        renderer is a rule the next renderer does not have.
        """
        if self.rated == 0:
            return "n=0 — no rated outcomes yet"

        if not self.sufficient_for_inference:
            return f"n={self.rated} — insufficient for inference"

        return f"n={self.rated}"


@dataclass(frozen=True, slots=True)
class GroupStats:
    """One group's record. Counts first, rate second, in that order deliberately."""

    successes: int
    failures: int
    expired: int
    invalidated: int

    @property
    def resolved(self) -> int:
        """Everything that reached a terminal state, rated or not."""

        return self.successes + self.failures + self.expired + self.invalidated

    @property
    def hit_rate(self) -> HitRate:
        rated = self.successes + self.failures

        if rated == 0:
            return HitRate(
                successes=self.successes,
                failures=self.failures,
                rated=0,
                rate=None,
                interval=None,
            )

        return HitRate(
            successes=self.successes,
            failures=self.failures,
            rated=rated,
            rate=_percent(Decimal(self.successes) / Decimal(rated)),
            interval=wilson_interval(self.successes, rated),
        )


def wilson_interval(successes: int, trials: int, *, z: Decimal = Z_95) -> Interval:
    """The Wilson score interval for a proportion.

    Chosen over the normal approximation because of how each behaves at the
    edges, which is where a young track record lives:

    * 9 of 9 — normal approximation: 100% ± 0. Wilson: about [70%, 100%].
    * 0 of 5 — normal approximation: 0% ± 0. Wilson: about [0%, 43%].

    A zero-width interval around an extreme is a claim of certainty, and this
    platform's whole position is that its record is its integrity.

    Raises on `trials <= 0` rather than returning an empty interval: an
    interval over nothing is not a wide interval, it is not an interval, and a
    caller that got [0, 1] back would render it as "somewhere between never and
    always" for a group that has no data at all.
    """
    if trials <= 0:
        raise ValueError("a Wilson interval needs at least one trial")

    if not 0 <= successes <= trials:
        raise ValueError(f"successes must be within 0..{trials}; got {successes}")

    # Widened for the intermediate arithmetic only. The default 28 digits is
    # ample for the result, but `p(1-p)/n + z²/4n²` under a square root loses
    # digits at both ends of the range, which is exactly where this interval is
    # asked to be trustworthy.
    with localcontext() as ctx:
        ctx.prec = 40

        n = Decimal(trials)
        p = Decimal(successes) / n
        z2 = z * z

        denominator = Decimal(1) + z2 / n
        centre = (p + z2 / (2 * n)) / denominator
        spread = (z / denominator) * (p * (Decimal(1) - p) / n + z2 / (4 * n * n)).sqrt()

        low = centre - spread
        high = centre + spread

    # Clamped because the algebra can step a hair outside [0, 1] at the
    # extremes, and a hit rate of "-0.00%" is not a number anyone should have
    # to explain.
    return Interval(
        low=_percent(max(Decimal(0), low)),
        high=_percent(min(Decimal(1), high)),
    )


def _percent(fraction: Decimal) -> Decimal:
    """A 0..1 fraction as a 0..100 percentage, to two places.

    Quantised at the boundary rather than left as a float: these numbers are
    the platform's public record, and two clients rendering `66.66666666667`
    differently is a difference in the track record as far as a reader is
    concerned.
    """
    return (fraction * 100).quantize(_PERCENT, rounding=ROUND_HALF_UP)
