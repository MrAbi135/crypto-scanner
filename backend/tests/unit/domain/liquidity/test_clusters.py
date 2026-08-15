"""EQH/EQL clustering tests."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from scanner.domain.liquidity import (
    LiquiditySide,
    detect_equal_level_clusters,
)
from scanner.domain.structure import (
    SwingKind,
    SwingPoint,
    SwingStrength,
)


def make_swing(
    index: int,
    price: str,
    kind: SwingKind,
) -> SwingPoint:
    return SwingPoint(
        index=index,
        open_time=datetime(
            2026,
            8,
            15,
            0,
            0,
            tzinfo=UTC,
        ),
        price=Decimal(price),
        kind=kind,
        strength=SwingStrength.INTERNAL,
    )


def test_equal_high_cluster_requires_intervening_depth() -> None:
    swings = (
        make_swing(2, "100", SwingKind.HIGH),
        make_swing(5, "98", SwingKind.LOW),
        make_swing(8, "100.02", SwingKind.HIGH),
    )

    clusters = detect_equal_level_clusters(
        swings,
        atr=Decimal("2"),
    )

    assert len(clusters) == 1
    assert clusters[0].side is LiquiditySide.BSL
    assert clusters[0].member_count == 2


def test_flat_shelf_is_not_cluster() -> None:
    swings = (
        make_swing(2, "100", SwingKind.HIGH),
        make_swing(5, "99.8", SwingKind.LOW),
        make_swing(8, "100.02", SwingKind.HIGH),
    )

    clusters = detect_equal_level_clusters(
        swings,
        atr=Decimal("2"),
    )

    assert clusters == ()
