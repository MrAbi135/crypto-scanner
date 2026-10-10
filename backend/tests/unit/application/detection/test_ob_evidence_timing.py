"""s6-ob-v9: an order block is decided with the evidence that existed when it was decided.

The no-repaint property in `tests/property/test_no_repaint_order_blocks.py`
found the defect on generated data, but it meets it rarely -- a prefix has to
end inside a four-candle window -- so the exact case is pinned here as well.

The case, as measured: a bullish displacement on candle d closes above an
older external high (OB_A), but a *higher* high printed on d-2. That pivot is
external only once five candles have closed after it, on d+3. The OB is
decided on d+1, so a live pass grades it OB_A. A pass over a longer window
used to pick "the latest external high" by the pivot's own time, found d-2,
saw the close below it, and graded the same OB_B.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import scanner.application.detection.ict_ob_replay as ob_mod
from scanner.application.detection.ict_ob_replay import SwingEvidence
from scanner.domain.ict import DisplacementDirection
from scanner.shared import Timeframe

TF = Timeframe.H1
HOUR = timedelta(hours=1)
D = datetime(2026, 10, 10, 12, tzinfo=UTC)  # the displacement candle's open
DECIDED = D + HOUR  # d+1: the candle that decides the OB


def _swing(at: datetime, price: int, strength: str) -> SwingEvidence:
    return SwingEvidence(at=at, index=0, price=Decimal(price), strength=strength, kind="HIGH")


OLDER_HIGH = _swing(D - 20 * HOUR, 100, "EXTERNAL")
HIGHER_HIGH = _swing(D - 2 * HOUR, 110, "EXTERNAL")


def _break_level(swings, decided_at):
    return ob_mod._latest_break_level(
        ob_mod._known_by(swings, decided_at, TF),
        strength="EXTERNAL",
        direction=DisplacementDirection.BULLISH,
        before_at=D,
    )


def test_a_pivot_not_yet_confirmed_at_the_decision_is_not_evidence() -> None:
    """Decided on d+1, the higher pivot at d-2 (confirmed on d+3) does not exist yet."""
    level = _break_level((OLDER_HIGH, HIGHER_HIGH), DECIDED)

    assert level == Decimal(100), "the OB was graded against a pivot it could not have known"
    assert ob_mod._closes_beyond_level(Decimal(105), DisplacementDirection.BULLISH, level)


def test_the_same_pivot_counts_once_it_is_confirmed() -> None:
    """The filter is about time, not about the pivot: on d+3 it is evidence."""
    assert _break_level((OLDER_HIGH, HIGHER_HIGH), D + 3 * HOUR) == Decimal(110)
    assert _break_level((OLDER_HIGH, HIGHER_HIGH), D + 2 * HOUR) == Decimal(100)


def test_every_swing_record_exists_from_its_fifth_right_hand_candle() -> None:
    """§3.1 (v1.0.9, ruling 2): a pivot's one record is written at its k = 5
    decision, internal or external alike.

    The first draft of v9 used k = 2 for an internal record -- §3.1's swing
    definition rather than its record timing -- and the no-repaint property
    caught it: a whole-series run held an internal low printed three candles
    before a displacement that a prefix had no record of yet.
    """
    internal = _swing(D - HOUR, 1, "INTERNAL")
    external = _swing(D - HOUR, 1, "EXTERNAL")

    assert ob_mod._known_by((internal, external), D + HOUR, TF) == ()
    assert ob_mod._known_by((internal, external), D + 3 * HOUR, TF) == ()
    assert ob_mod._known_by((internal, external), D + 4 * HOUR, TF) == (internal, external)


def test_an_ob_is_decided_on_the_candle_after_its_displacement() -> None:
    """Derived from time, so it is defined even when d is the newest candle."""
    from tests.support.builders import make_candle

    candles = [make_candle(timeframe=TF, open_time=D + i * HOUR) for i in range(3)]

    assert ob_mod._ob_decided_at(candles, 2) == candles[2].open_time + HOUR
