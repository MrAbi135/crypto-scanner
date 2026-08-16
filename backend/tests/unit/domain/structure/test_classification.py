"""Tests for SLS §3.3 swing classification."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from scanner.domain.structure import (
    StructureLabel,
    SwingKind,
    SwingPoint,
    SwingStrength,
    classify_swings,
)


def swing(
    index: int,
    *,
    price: str,
    kind: SwingKind,
) -> SwingPoint:
    return SwingPoint(
        index=index,
        open_time=datetime(
            2026,
            8,
            1,
            index,
            tzinfo=UTC,
        ),
        price=Decimal(price),
        kind=kind,
        strength=SwingStrength.EXTERNAL,
    )


def test_highs_classify_hh_lh_and_eqh() -> None:
    swings = [
        swing(
            1,
            price="100",
            kind=SwingKind.HIGH,
        ),
        swing(
            3,
            price="110",
            kind=SwingKind.HIGH,
        ),
        swing(
            5,
            price="105",
            kind=SwingKind.HIGH,
        ),
        swing(
            7,
            price="105",
            kind=SwingKind.HIGH,
        ),
    ]

    result = classify_swings(swings)

    assert [item.label for item in result] == [
        StructureLabel.SEED,
        StructureLabel.HH,
        StructureLabel.LH,
        StructureLabel.EQH,
    ]


def test_lows_classify_hl_ll_and_eql() -> None:
    swings = [
        swing(
            1,
            price="100",
            kind=SwingKind.LOW,
        ),
        swing(
            3,
            price="105",
            kind=SwingKind.LOW,
        ),
        swing(
            5,
            price="95",
            kind=SwingKind.LOW,
        ),
        swing(
            7,
            price="95",
            kind=SwingKind.LOW,
        ),
    ]

    result = classify_swings(swings)

    assert [item.label for item in result] == [
        StructureLabel.SEED,
        StructureLabel.HL,
        StructureLabel.LL,
        StructureLabel.EQL,
    ]


def test_highs_and_lows_use_separate_predecessors() -> None:
    swings = [
        swing(
            1,
            price="100",
            kind=SwingKind.HIGH,
        ),
        swing(
            2,
            price="50",
            kind=SwingKind.LOW,
        ),
        swing(
            3,
            price="110",
            kind=SwingKind.HIGH,
        ),
        swing(
            4,
            price="55",
            kind=SwingKind.LOW,
        ),
    ]

    result = classify_swings(swings)

    assert [item.label for item in result] == [
        StructureLabel.SEED,
        StructureLabel.SEED,
        StructureLabel.HH,
        StructureLabel.HL,
    ]


def test_epsilon_creates_equal_high_and_low_band() -> None:
    swings = [
        swing(
            1,
            price="100",
            kind=SwingKind.HIGH,
        ),
        swing(
            2,
            price="50",
            kind=SwingKind.LOW,
        ),
        swing(
            3,
            price="100.05",
            kind=SwingKind.HIGH,
        ),
        swing(
            4,
            price="49.95",
            kind=SwingKind.LOW,
        ),
    ]

    result = classify_swings(
        swings,
        epsilon=Decimal("0.1"),
    )

    assert [item.label for item in result] == [
        StructureLabel.SEED,
        StructureLabel.SEED,
        StructureLabel.EQH,
        StructureLabel.EQL,
    ]


def test_negative_epsilon_is_rejected() -> None:
    with pytest.raises(
        ValueError,
        match="epsilon must be non-negative",
    ):
        classify_swings(
            [],
            epsilon=Decimal("-1"),
        )
