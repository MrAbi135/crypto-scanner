"""Factor weights and grade bands (SLS §9.1, §9.4).

Values copied from the spec, not chosen here. §9.1 makes them
`P.rank.weights`, versioned, and says plainly that changing one requires a spec
amendment plus full golden-dataset and outcome re-validation (Constitution
§30.8) -- so this module is a transcription with a guard, not a design.
"""

from __future__ import annotations

from decimal import Decimal
from enum import Enum


class Factor(str, Enum):
    """§8.3 evidence factors."""

    STRUCTURE = "F1"
    LIQUIDITY = "F2"
    ZONE = "F3"
    VOLUME = "F4"
    MOMENTUM = "F5"
    HTF_ALIGNMENT = "F6"


# §9.1. Structure and Zone lead at 20% each -- context quality and location
# quality are the two halves of the entry thesis. Volume sits at 15% rather
# than the 20% a naive design would give it, and the spec is explicit that this
# is defensive: crypto volume is the most manipulable input, so the weight
# bounds the damage of whatever slips past §6.6.
WEIGHTS: dict[Factor, Decimal] = {
    Factor.STRUCTURE: Decimal("0.20"),
    Factor.ZONE: Decimal("0.20"),
    Factor.LIQUIDITY: Decimal("0.15"),
    Factor.VOLUME: Decimal("0.15"),
    Factor.MOMENTUM: Decimal("0.15"),
    Factor.HTF_ALIGNMENT: Decimal("0.15"),
}


def assert_weights_sum_to_one() -> None:
    """A drifted weight table silently rescales every score ever published.

    Called at import so the failure is a boot error rather than a slow bias
    nobody can see in the output.
    """
    total = sum(WEIGHTS.values(), Decimal(0))

    if total != Decimal(1):
        raise ValueError(f"factor weights must sum to 1.00, got {total}")


assert_weights_sum_to_one()


class Grade(str, Enum):
    S = "S"
    A = "A"
    B = "B"


GRADE_S_FLOOR = Decimal(90)
GRADE_A_FLOOR = Decimal(80)
GRADE_B_FLOOR = Decimal(70)


def grade(final_confidence: Decimal) -> Grade | None:
    """§9.4. Below 70 is not a weak grade -- it is not published at all."""
    if final_confidence >= GRADE_S_FLOOR:
        return Grade.S

    if final_confidence >= GRADE_A_FLOOR:
        return Grade.A

    if final_confidence >= GRADE_B_FLOOR:
        return Grade.B

    return None
