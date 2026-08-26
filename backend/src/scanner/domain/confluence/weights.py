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


# §9.1's "Why this weight exists" column, transcribed. §18.6 serves these as a
# "doctrine transparency endpoint", which only means anything if the words are
# the doctrine's own — a paraphrase would let the published justification drift
# from the rule it justifies, and a reader has no way to tell.
#
# Kept beside `WEIGHTS` so a weight cannot be changed without the sentence that
# defends it being right there; `assert_every_weight_is_justified` makes adding
# one without the other a boot error.
FACTOR_JUSTIFICATION: dict[Factor, str] = {
    Factor.STRUCTURE: (
        "Structure is the doctrine's context spine: every other factor is "
        "interpreted *through* it. It cannot dominate alone (a clean trend "
        "with no entry logic is not a setup), but nothing outranks it — a "
        "setup against structure is not a setup at all (enforced by gate G2, "
        "so the weight expresses *quality*, not permission)."
    ),
    Factor.ZONE: (
        "The zone is the entry edge — it defines *where* risk is placed and "
        "why the location is defensible. Equal to structure because location "
        "quality and context quality are the two halves of the institutional "
        "entry thesis."
    ),
    Factor.LIQUIDITY: (
        "The narrative driver (what was engineered, what is targeted). "
        "Weighted below structure/zone because liquidity evidence is already "
        "partially embedded in MSS/breaker/stop-hunt construction — full "
        "weight would double-count the sweep chain (the synergy bonus §8.5 "
        "rewards the *intact chain* explicitly instead)."
    ),
    Factor.VOLUME: (
        "Participation confirms institutional presence — but crypto volume is "
        "the most manipulable factor (§6.6). Deliberately **below** the 20% a "
        "naive design would assign: the fake-volume defense caps corrupt "
        "inputs, and the reduced weight bounds the damage of what slips "
        "through. This is a defensive weighting decision, not a statement "
        "that volume matters little."
    ),
    Factor.MOMENTUM: (
        "Timing quality: energy alignment separates a zone that will be "
        "defended from one that will be sliced through. Kept moderate because "
        "momentum is the most transient factor — it decays within candles, "
        "and over-weighting it biases the scanner toward chasing."
    ),
    Factor.HTF_ALIGNMENT: (
        "Top-down doctrine made numeric. Material enough that counter-HTF "
        "continuation setups can rarely reach publication floors; bounded "
        "because the gate system (G2) and archetype rules already encode the "
        "hard constraint — the weight prices *degree* of alignment (aligned "
        "vs. caution vs. ranging)."
    ),
}


def assert_every_weight_is_justified() -> None:
    """A weight with no stated reason is a number nobody can argue with.

    §9.1 pairs every weight with its justification and §18.6 publishes both.
    Checked at import, like the sum, so adding a factor without its reasoning
    fails at boot rather than serving a blank cell to a reader who came
    specifically to see it.
    """
    missing = sorted(f.value for f in WEIGHTS if not FACTOR_JUSTIFICATION.get(f))

    if missing:
        raise ValueError(f"factors carry a weight with no §9.1 justification: {missing}")

    orphaned = sorted(f.value for f in FACTOR_JUSTIFICATION if f not in WEIGHTS)

    if orphaned:
        raise ValueError(f"justifications for factors that carry no weight: {orphaned}")


assert_every_weight_is_justified()


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
