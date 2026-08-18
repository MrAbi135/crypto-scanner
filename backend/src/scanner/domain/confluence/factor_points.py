"""Factor point tables (SLS §8.3.1, added v1.0.5).

Transcribed from the amendment, not designed here. Each factor is the sum of
its components, clamped to [0,100], and every component carries the evidence id
of the fact that awarded it — §8.3's "reproducible from the evidence alone".

The component ranges of each factor sum to exactly 100, asserted at import. A
table that cannot reach its own ceiling silently caps a factor below the weight
§9.1 assigns it, which would rescale every score without changing anything
visible.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from scanner.domain.confluence.factors import Contribution, FactorScore, from_contributions
from scanner.domain.confluence.weights import Factor

# ---------------------------------------------------------------- F1 structure
BREAK_CONFIRMED = Decimal(15)
BREAK_DISPLACED = Decimal(18)
BREAK_EXTERNAL = Decimal(12)
MSS_PRESENT = Decimal(10)
TREND_MATURITY_MAX = Decimal(30)
TREND_MATURITY_FULL_PAIRS = 4
CLEAN_RECORD_FULL = Decimal(15)
CLEAN_RECORD_ONE_FAILURE = Decimal(7)

# ---------------------------------------------------------------- F2 liquidity
SWEEP_CONFIRMED = Decimal(20)
SWEEP_EXTERNAL = Decimal(16)
SWEEP_DEPTH_MAX = Decimal(12)
SWEEP_UNCLAIMED = Decimal(6)
SWEEP_FRESH = Decimal(6)
STOP_HUNT_CONFIRMED = Decimal(15)
TARGET_POOL_MAX = Decimal(25)

# --------------------------------------------------------------------- F3 zone
ZONE_GRADE_POINTS: dict[str, Decimal] = {
    "BRK_A": Decimal(50),
    "OB_A": Decimal(40),
    "OB_B": Decimal(32),
    "FVG": Decimal(25),
    "MIT": Decimal(18),
    "IFVG": Decimal(10),
}
ZONE_STATE_POINTS: dict[str, Decimal] = {
    "FRESH": Decimal(25),
    "TESTED": Decimal(15),
    "CE_FILLED": Decimal(6),
}
ZONE_STACK_POINTS = Decimal(15)
ZONE_CONFIRMATION = Decimal(10)

# ----------------------------------------------------------------- F5 momentum
MOMENTUM_ALIGNED_FACTOR = Decimal("0.55")
ACCEL_ACCELERATING = Decimal(25)
ACCEL_STEADY = Decimal(12)
NO_EXHAUSTION = Decimal(20)


def _assert_reaches_one_hundred() -> None:
    totals = {
        "F1": BREAK_CONFIRMED + BREAK_DISPLACED + BREAK_EXTERNAL + MSS_PRESENT + TREND_MATURITY_MAX,
        "F2": SWEEP_CONFIRMED
        + SWEEP_EXTERNAL
        + SWEEP_DEPTH_MAX
        + SWEEP_UNCLAIMED
        + SWEEP_FRESH
        + STOP_HUNT_CONFIRMED
        + TARGET_POOL_MAX,
        "F3": max(ZONE_GRADE_POINTS.values())
        + max(ZONE_STATE_POINTS.values())
        + ZONE_STACK_POINTS
        + ZONE_CONFIRMATION,
        "F5": Decimal(100) * MOMENTUM_ALIGNED_FACTOR + ACCEL_ACCELERATING + NO_EXHAUSTION,
    }

    # F1 omits its clean-record term above because a perfect break record and a
    # perfect everything-else already reach 100 without it -- that is the one
    # table where the components deliberately overlap, so it is checked
    # separately rather than silently.
    if totals["F1"] != Decimal(85):
        raise ValueError(f"F1 core components must reach 85, got {totals['F1']}")

    for name in ("F2", "F3", "F5"):
        if totals[name] != Decimal(100):
            raise ValueError(f"{name} components must reach 100, got {totals[name]}")


_assert_reaches_one_hundred()


@dataclass(frozen=True, slots=True)
class StructureEvidence:
    break_confirmed: bool = False
    displaced: bool = False
    external: bool = False
    mss: bool = False
    unbroken_pairs: int = 0
    failed_breaks: int = 0
    evidence_ids: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class LiquidityEvidence:
    sweep_confirmed: bool = False
    external: bool = False
    depth_atr: Decimal = Decimal(0)
    unclaimed: bool = False
    fresh: bool = False
    stop_hunt: bool = False
    target_pool_strength: Decimal = Decimal(0)
    evidence_ids: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ZoneEvidence:
    grade: str | None = None
    state: str | None = None
    stack_depth: int = 1
    entry_confirmation: bool = False
    evidence_ids: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class MomentumEvidence:
    score: Decimal = Decimal(0)
    aligned: bool = False
    accelerating: bool = False
    decelerating: bool = False
    exhaustion_against: bool = False
    evidence_ids: dict[str, str] = field(default_factory=dict)


def _contrib(
    ids: dict[str, str],
    code: str,
    points: Decimal,
) -> Contribution:
    return Contribution(code, points, ids.get(code))


def structure_factor(e: StructureEvidence) -> FactorScore:
    """F1 — §8.3.1."""
    parts: list[Contribution] = []

    if e.break_confirmed:
        parts.append(_contrib(e.evidence_ids, "break_confirmed", BREAK_CONFIRMED))

        if e.displaced:
            parts.append(_contrib(e.evidence_ids, "displaced", BREAK_DISPLACED))

        if e.external:
            parts.append(_contrib(e.evidence_ids, "external", BREAK_EXTERNAL))

        if e.mss:
            parts.append(_contrib(e.evidence_ids, "mss", MSS_PRESENT))

    pairs = max(0, min(e.unbroken_pairs, TREND_MATURITY_FULL_PAIRS))

    if pairs:
        parts.append(
            _contrib(
                e.evidence_ids,
                "trend_maturity",
                TREND_MATURITY_MAX * Decimal(pairs) / Decimal(TREND_MATURITY_FULL_PAIRS),
            )
        )

    if e.failed_breaks <= 0:
        parts.append(_contrib(e.evidence_ids, "clean_record", CLEAN_RECORD_FULL))
    elif e.failed_breaks == 1:
        parts.append(_contrib(e.evidence_ids, "clean_record", CLEAN_RECORD_ONE_FAILURE))

    return from_contributions(Factor.STRUCTURE, tuple(parts))


def liquidity_factor(e: LiquidityEvidence) -> FactorScore:
    """F2 — §8.3.1."""
    parts: list[Contribution] = []

    if e.sweep_confirmed:
        parts.append(_contrib(e.evidence_ids, "sweep_confirmed", SWEEP_CONFIRMED))

        if e.external:
            parts.append(_contrib(e.evidence_ids, "external", SWEEP_EXTERNAL))

        depth = min(max(e.depth_atr, Decimal(0)), Decimal(1))

        if depth > 0:
            parts.append(_contrib(e.evidence_ids, "depth", SWEEP_DEPTH_MAX * depth))

        # §4.6 calls a reclaimed sweep contrary evidence, so the whole award is
        # withheld rather than scaled -- partial credit would still read as
        # support for the setup it undermines.
        if e.unclaimed:
            parts.append(_contrib(e.evidence_ids, "unclaimed", SWEEP_UNCLAIMED))

        if e.fresh:
            parts.append(_contrib(e.evidence_ids, "fresh", SWEEP_FRESH))

    if e.stop_hunt:
        parts.append(_contrib(e.evidence_ids, "stop_hunt", STOP_HUNT_CONFIRMED))

    strength = min(max(e.target_pool_strength, Decimal(0)), Decimal(100))

    if strength > 0:
        parts.append(
            _contrib(e.evidence_ids, "target_pool", TARGET_POOL_MAX * strength / Decimal(100))
        )

    return from_contributions(Factor.LIQUIDITY, tuple(parts))


def zone_factor(e: ZoneEvidence) -> FactorScore:
    """F3 — §8.3.1."""
    parts: list[Contribution] = []

    if e.grade in ZONE_GRADE_POINTS:
        parts.append(_contrib(e.evidence_ids, "grade", ZONE_GRADE_POINTS[e.grade]))

    if e.state in ZONE_STATE_POINTS:
        parts.append(_contrib(e.evidence_ids, "state", ZONE_STATE_POINTS[e.state]))

    # Stops paying after two: §8.5 already awards a zone-stack synergy bonus,
    # and an unbounded term would reward piling weak zones together.
    if e.stack_depth >= 2:
        parts.append(_contrib(e.evidence_ids, "stack", ZONE_STACK_POINTS))

    if e.entry_confirmation:
        parts.append(_contrib(e.evidence_ids, "entry_confirmation", ZONE_CONFIRMATION))

    return from_contributions(Factor.ZONE, tuple(parts))


def momentum_factor(e: MomentumEvidence) -> FactorScore:
    """F5 — §8.3.1."""
    parts: list[Contribution] = []

    # Opposed or neutral momentum scores nothing here rather than scaling down:
    # §7.1 already forces NEUTRAL where no direction dominates, so a score
    # pointing the other way is absence of support, not weak support.
    if e.aligned:
        aligned = min(max(e.score, Decimal(0)), Decimal(100)) * MOMENTUM_ALIGNED_FACTOR

        if aligned > 0:
            parts.append(_contrib(e.evidence_ids, "aligned_momentum", aligned))

    if e.accelerating:
        parts.append(_contrib(e.evidence_ids, "acceleration", ACCEL_ACCELERATING))
    elif not e.decelerating:
        parts.append(_contrib(e.evidence_ids, "acceleration", ACCEL_STEADY))

    if not e.exhaustion_against:
        parts.append(_contrib(e.evidence_ids, "no_exhaustion", NO_EXHAUSTION))

    return from_contributions(Factor.MOMENTUM, tuple(parts))
