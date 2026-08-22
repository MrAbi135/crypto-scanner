"""§6.6 Fake Volume Defense — the composite, and the hysteresis around it.

Four tests, twenty-five points each, and a threshold of fifty. §6.6 states the
consequence of that arithmetic separately -- "no single test may tag a symbol"
-- because the arithmetic is what enforces it, and a later change to the point
values could quietly stop enforcing it. `tags_wash_risk` asserts the rule
rather than restating it.

A test that could not be run is neither passed nor failed. Two of the four
need data this build only recently began collecting, and a symbol whose depth
was never sampled has not been shown to have honest volume -- nor dishonest.
Unmeasured tests simply do not score, which makes the tag harder to earn on a
thinly-observed symbol. That is the right direction: §6.6 caps a symbol's whole
volume factor and its alert priority, and it should take evidence to do that.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

# §6.6: "four tests, 25 points each ... Score >= 50 => symbol tagged".
TEST_POINTS = Decimal(25)
WASH_RISK_AT = Decimal(50)

# §6.6: "tag lifts after 3 consecutive clean days (hysteresis, §1.4 pattern)".
CLEAN_DAYS_TO_LIFT = 3

# §6.6's own thresholds.
ROUND_TRIP_DELTA_RATIO = Decimal("0.02")
TRADE_SIZE_CV = Decimal("0.2")
SUSPECT_CANDLES_24H = 5

# §1.8: "meme-category symbols get test thresholds tightened by 20%". Tighter
# means easier to trip, so the thresholds a symbol must stay *under* widen.
MEME_THRESHOLD_MULTIPLIER = Decimal("1.2")


@dataclass(frozen=True, slots=True)
class FakeVolumeTests:
    """§6.6's four tests. None where the input was not available."""

    volume_unsupported_by_depth: bool | None = None
    round_trip_symmetry: bool | None = None
    trade_size_uniformity: bool | None = None
    excess_suspect_candles: bool | None = None

    @property
    def failed(self) -> int:
        return sum(1 for value in self._all if value is True)

    @property
    def measured(self) -> int:
        return sum(1 for value in self._all if value is not None)

    @property
    def _all(self) -> tuple[bool | None, ...]:
        return (
            self.volume_unsupported_by_depth,
            self.round_trip_symmetry,
            self.trade_size_uniformity,
            self.excess_suspect_candles,
        )


def fake_volume_score(tests: FakeVolumeTests) -> Decimal:
    """§6.6's 0-100 score: twenty-five points per failed test."""
    return TEST_POINTS * Decimal(tests.failed)


def tags_wash_risk(tests: FakeVolumeTests) -> bool:
    """§6.6: "Score >= 50 => symbol tagged `wash_risk`"."""
    tagged = fake_volume_score(tests) >= WASH_RISK_AT

    # §6.6: "the composite (need >= 2 tests) ... no single test may tag a
    # symbol". At twenty-five points a piece the threshold already says this,
    # and the assertion is here so that stops being true loudly rather than
    # silently if the point values ever move.
    assert not tagged or tests.failed >= 2, "a single failed test must not tag a symbol"

    return tagged


def round_trip_symmetry(
    *,
    absolute_delta: Decimal,
    total_volume: Decimal,
    rvol_elevated: bool,
    meme: bool = False,
) -> bool | None:
    """§6.6(2): "daily |cum delta| / total volume < 0.02 with elevated RVOL".

    Both halves. A perfectly two-sided tape on a quiet day is a quiet day; it
    is the symmetry *under elevated volume* that has no honest explanation.
    """
    if total_volume <= 0:
        return None

    if not rvol_elevated:
        return False

    threshold = ROUND_TRIP_DELTA_RATIO * (MEME_THRESHOLD_MULTIPLIER if meme else 1)

    return absolute_delta / total_volume < threshold


def trade_size_uniformity(
    *,
    mean_trade_size: Decimal,
    stddev_trade_size: Decimal,
    meme: bool = False,
) -> bool | None:
    """§6.6(3): "coefficient of variation of trade sizes < 0.2"."""
    if mean_trade_size <= 0:
        return None

    threshold = TRADE_SIZE_CV * (MEME_THRESHOLD_MULTIPLIER if meme else 1)

    return stddev_trade_size / mean_trade_size < threshold


def excess_suspect_candles(count: int, *, meme: bool = False) -> bool:
    """§6.6(4): "`suspect_volume` candle count (§6.4) > 5 in 24h"."""
    if meme:
        # A count is tightened by lowering it, not by raising it: the symbol
        # trips on fewer suspect candles.
        return Decimal(count) > Decimal(SUSPECT_CANDLES_24H) / MEME_THRESHOLD_MULTIPLIER

    return count > SUSPECT_CANDLES_24H


@dataclass(frozen=True, slots=True)
class WashRiskState:
    """The tag and the clean-day count §6.6 lifts it on."""

    tagged: bool = False
    clean_days: int = 0


def evaluate_wash_risk(state: WashRiskState, tagged_today: bool) -> WashRiskState:
    """One daily evaluation, with §6.6's hysteresis.

    Tagging is immediate and lifting is not: §6.6 gives three consecutive
    clean days, on §1.4's pattern, because the tests it composes can be
    tripped by legitimate high-frequency market-making on any single day.
    """
    if tagged_today:
        return WashRiskState(tagged=True, clean_days=0)

    if not state.tagged:
        return WashRiskState(tagged=False, clean_days=0)

    clean = state.clean_days + 1

    if clean >= CLEAN_DAYS_TO_LIFT:
        return WashRiskState(tagged=False, clean_days=0)

    return WashRiskState(tagged=True, clean_days=clean)
