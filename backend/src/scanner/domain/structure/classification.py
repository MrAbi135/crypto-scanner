"""HH/HL/LH/LL classification (SLS §3.3)."""

from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal

from scanner.domain.structure.model import (
    ClassifiedSwing,
    StructureLabel,
    SwingKind,
    SwingPoint,
)


def classify_swings(
    swings: Sequence[SwingPoint],
    *,
    epsilon: Decimal = Decimal("0"),
) -> tuple[ClassifiedSwing, ...]:
    """Classify swings against their immediate same-kind predecessor."""

    if epsilon < 0:
        raise ValueError("epsilon must be non-negative")

    previous_high: SwingPoint | None = None
    previous_low: SwingPoint | None = None
    classified: list[ClassifiedSwing] = []

    for swing in sorted(
        swings,
        key=lambda item: (
            item.index,
            item.kind.value,
        ),
    ):
        if swing.kind is SwingKind.HIGH:
            if previous_high is not None:
                classified.append(
                    ClassifiedSwing(
                        swing=swing,
                        label=_classify_high(
                            previous_high.price,
                            swing.price,
                            epsilon,
                        ),
                    )
                )

            previous_high = swing
            continue

        if previous_low is not None:
            classified.append(
                ClassifiedSwing(
                    swing=swing,
                    label=_classify_low(
                        previous_low.price,
                        swing.price,
                        epsilon,
                    ),
                )
            )

        previous_low = swing

    return tuple(classified)


def _classify_high(
    previous: Decimal,
    current: Decimal,
    epsilon: Decimal,
) -> StructureLabel:
    if current > previous + epsilon:
        return StructureLabel.HH

    if current < previous - epsilon:
        return StructureLabel.LH

    return StructureLabel.EQH


def _classify_low(
    previous: Decimal,
    current: Decimal,
    epsilon: Decimal,
) -> StructureLabel:
    if current > previous + epsilon:
        return StructureLabel.HL

    if current < previous - epsilon:
        return StructureLabel.LL

    return StructureLabel.EQL
