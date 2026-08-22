"""§7.4 trend-maturity pair counting."""

from __future__ import annotations

from scanner.domain.structure import StructureLabel, unbroken_pairs


def test_unbroken_pairs_counts_the_current_run_not_the_best_one() -> None:
    """§7.4 measures trend *maturity* — how long the run has held.

    Taking the longest run anywhere in the window would let a trend that broke
    fifty candles ago still pay the full 30 points of F1.
    """
    labels = [
        # A long uptrend run that is over.
        StructureLabel.HH,
        StructureLabel.HL,
        StructureLabel.HH,
        StructureLabel.HL,
        # Broken.
        StructureLabel.LH,
        # The current run: one pair.
        StructureLabel.HH,
        StructureLabel.HL,
    ]

    assert unbroken_pairs(labels, "UP") == 1


def test_a_single_trailing_swing_is_not_yet_a_pair() -> None:
    assert unbroken_pairs([StructureLabel.LH, StructureLabel.HH], "UP") == 0


def test_an_equal_extreme_breaks_the_run() -> None:
    """EQH asserts no direction, so it cannot extend a directional run."""
    labels = [StructureLabel.HH, StructureLabel.HL, StructureLabel.EQH]

    assert unbroken_pairs(labels, "UP") == 0


def test_a_seed_breaks_the_run_too() -> None:
    """§3.3: SEED establishes a reference point, it does not assert a direction."""
    assert unbroken_pairs([StructureLabel.HH, StructureLabel.SEED], "UP") == 0


def test_the_short_side_reads_the_mirror_labels() -> None:
    labels = [StructureLabel.LL, StructureLabel.LH, StructureLabel.LL, StructureLabel.LH]

    assert unbroken_pairs(labels, "DOWN") == 2
    assert unbroken_pairs(labels, "UP") == 0
