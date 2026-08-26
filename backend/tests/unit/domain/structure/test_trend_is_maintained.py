"""§3.4's state is maintained, not re-derived.

§3.4 opens with "Maintain **one authoritative** directional state per symbol
per TF", and its diagram leaves `BULLISH` by exactly two edges: to
`BULLISH_CAUTION` on a CHoCH, and to `RANGING` on the idle rule. There is no
edge that fires because the last two labels stopped agreeing.

The entry rule used to live outside the machine, in two separate functions
returning two different types, and the BOS gate consulted the one with no
memory. A single LH after a run of HH dropped that gate straight back to
RANGING.

**What this file does not assert.** The change was first justified by a
measurement of "zero BOS on H1 and H4" that turned out to be a truncated query
— the host held 63 on H1 and 5 on H4. The properties below are demonstrated
from §3.4 itself and stand on their own; the production impact is unquantified
and deliberately unclaimed.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from scanner.domain.structure import TrendState, TrendStateMachine
from scanner.domain.structure.breaks import BreakDirection
from scanner.domain.structure.model import (
    ClassifiedSwing,
    StructureLabel,
    SwingKind,
    SwingPoint,
    SwingStrength,
)

T0 = datetime(2026, 8, 26, tzinfo=UTC)


def labelled(label: StructureLabel, index: int) -> ClassifiedSwing:
    kind = (
        SwingKind.HIGH
        if label in {StructureLabel.HH, StructureLabel.LH, StructureLabel.EQH}
        else SwingKind.LOW
    )

    return ClassifiedSwing(
        swing=SwingPoint(
            index=index,
            open_time=T0 + timedelta(hours=index),
            price=Decimal(100 + index),
            kind=kind,
            strength=SwingStrength.EXTERNAL,
        ),
        label=label,
    )


def bullish_chain() -> list[ClassifiedSwing]:
    """Two consecutive HH and two consecutive HL — §3.4's entry condition."""

    return [
        labelled(StructureLabel.HH, 1),
        labelled(StructureLabel.HL, 2),
        labelled(StructureLabel.HH, 3),
        labelled(StructureLabel.HL, 4),
    ]


def test_two_consecutive_pairs_enter_the_trend() -> None:
    machine = TrendStateMachine()

    assert machine.apply_structure(bullish_chain()) is TrendState.BULLISH


def test_the_mirror_enters_bearish() -> None:
    machine = TrendStateMachine()

    chain = [
        labelled(StructureLabel.LH, 1),
        labelled(StructureLabel.LL, 2),
        labelled(StructureLabel.LH, 3),
        labelled(StructureLabel.LL, 4),
    ]

    assert machine.apply_structure(chain) is TrendState.BEARISH


def test_a_single_contrary_label_does_not_end_the_trend() -> None:
    """The defect, stated as a property.

    A market in a genuine trend prints an occasional lower high without
    ceasing to trend. Re-deriving from the last two labels turned each of
    those into an immediate drop to RANGING — an edge §3.4's diagram does not
    draw.
    """
    machine = TrendStateMachine()

    machine.apply_structure(bullish_chain())

    assert machine.state is TrendState.BULLISH

    # One LH prints. Under the old stateless rule this was RANGING.
    interrupted = [*bullish_chain(), labelled(StructureLabel.LH, 5)]

    assert machine.apply_structure(interrupted) is TrendState.BULLISH


def test_the_trend_survives_a_run_of_contrary_labels() -> None:
    """Not merely one. Leaving a trend is a CHoCH or the idle rule, and
    neither is "the last two labels disagree"."""

    machine = TrendStateMachine()
    machine.apply_structure(bullish_chain())

    contrary = [
        *bullish_chain(),
        labelled(StructureLabel.LH, 5),
        labelled(StructureLabel.LL, 6),
        labelled(StructureLabel.LH, 7),
        labelled(StructureLabel.LL, 8),
    ]

    # Even a full opposing chain does not flip it: §3.4 flips only through
    # CAUTION and a confirmed MSS.
    assert machine.apply_structure(contrary) is TrendState.BULLISH


def test_only_ranging_has_an_entry_edge() -> None:
    """§3.4 draws entry from RANGING and from CAUTION-via-MSS, nowhere else.

    A machine in CAUTION must not be re-entered by structure alone — that
    would let a trend under question restore itself without the confirmation
    §3.6 requires.
    """
    machine = TrendStateMachine(state=TrendState.BULLISH_CAUTION)

    assert machine.apply_structure(bullish_chain()) is TrendState.BULLISH_CAUTION


def test_an_incomplete_chain_stays_ranging() -> None:
    machine = TrendStateMachine()

    assert machine.apply_structure([]) is TrendState.RANGING
    assert machine.apply_structure([labelled(StructureLabel.HH, 1)]) is TrendState.RANGING
    # Two HH but only one HL: the rule needs both pairs.
    partial = [
        labelled(StructureLabel.HH, 1),
        labelled(StructureLabel.HH, 2),
        labelled(StructureLabel.HL, 3),
    ]

    assert machine.apply_structure(partial) is TrendState.RANGING


def test_a_mixed_chain_does_not_enter() -> None:
    """Entry is strict; it is *staying* that was wrong.

    This keeps the fix honest — it would be easy to fix the persistence by
    loosening the entry, and that would publish setups on structure the
    doctrine does not recognise.
    """
    machine = TrendStateMachine()

    mixed = [
        labelled(StructureLabel.HH, 1),
        labelled(StructureLabel.HL, 2),
        labelled(StructureLabel.LH, 3),
        labelled(StructureLabel.HL, 4),
    ]

    assert machine.apply_structure(mixed) is TrendState.RANGING


@pytest.mark.parametrize(
    ("entered", "choch", "expected"),
    [
        (TrendState.BULLISH, BreakDirection.DOWN, TrendState.BULLISH_CAUTION),
        (TrendState.BEARISH, BreakDirection.UP, TrendState.BEARISH_CAUTION),
    ],
)
def test_the_documented_exit_still_works(
    entered: TrendState, choch: BreakDirection, expected: TrendState
) -> None:
    """The edge §3.4 *does* draw out of a trend, unchanged by this."""

    machine = TrendStateMachine(state=entered)

    assert machine.apply_choch(choch) is expected
