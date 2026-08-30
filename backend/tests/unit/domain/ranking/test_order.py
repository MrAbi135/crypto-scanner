"""§9.2's five-key cross-symbol order."""

from __future__ import annotations

import random
from decimal import Decimal

from scanner.domain.common.universe import UniverseTier
from scanner.domain.confluence import Archetype
from scanner.domain.ranking import RankableSetup, rank
from scanner.shared import Timeframe


def setup(
    symbol: str,
    *,
    confidence: str = "80",
    archetype: Archetype = Archetype.FVG_CONTINUATION,
    timeframe: Timeframe = Timeframe.H1,
    tier: UniverseTier = UniverseTier.T2,
    direction: str = "UP",
) -> RankableSetup:
    return RankableSetup(
        symbol=symbol,
        timeframe=timeframe,
        confidence=Decimal(confidence),
        archetype=archetype,
        tier=tier,
        direction=direction,
    )


def test_confidence_orders_before_every_tie_break() -> None:
    """§9.2(1): FinalConfidence descending, and nothing outranks it."""
    ordered = rank(
        [
            # Everything a tie-break could prefer, on the weaker setup.
            setup(
                "AAAUSDT",
                confidence="79",
                archetype=Archetype.SWEEP_REVERSAL,
                timeframe=Timeframe.D1,
                tier=UniverseTier.T1,
            ),
            setup("ZZZUSDT", confidence="80"),
        ]
    )

    assert [s.symbol for s in ordered] == ["ZZZUSDT", "AAAUSDT"]


def test_archetype_priority_is_not_the_classification_order() -> None:
    """§9.2(2): A1 > A2 > A5 > A3 > A4.

    A5 sits between A2 and A3, which is the whole reason this is a separate
    order from §8.6's first-match classification sequence. A test that only
    checked A1 first and A4 last would pass against the classification order
    too, and would be checking nothing.
    """
    ordered = rank(
        [
            setup("E", archetype=Archetype.FVG_CONTINUATION),
            setup("C", archetype=Archetype.RANGE_LIQUIDITY_PLAY),
            setup("A", archetype=Archetype.SWEEP_REVERSAL),
            setup("D", archetype=Archetype.CONTINUATION_PULLBACK),
            setup("B", archetype=Archetype.BREAKER_RETEST),
        ]
    )

    assert [s.symbol for s in ordered] == ["A", "B", "C", "D", "E"]


def test_higher_timeframe_wins_and_it_is_the_ladder_not_the_enum() -> None:
    """§9.2(3): higher TF.

    "Higher" is the ladder position, so H4 outranks H1 by duration. Ordering
    on the enum's declaration order would agree here by accident, so the
    assertion below also pins D1 above M5 -- the pair furthest apart, where a
    wrong key is hardest to hide.
    """
    ordered = rank(
        [
            setup("C", timeframe=Timeframe.M5),
            setup("A", timeframe=Timeframe.D1),
            setup("B", timeframe=Timeframe.H4),
        ]
    )

    assert [s.symbol for s in ordered] == ["A", "B", "C"]


def test_liquidity_tier_breaks_a_tie_the_timeframe_cannot() -> None:
    """§9.2(4): higher liquidity tier, T1 first."""
    ordered = rank(
        [
            setup("C", tier=UniverseTier.T3),
            setup("A", tier=UniverseTier.T1),
            setup("B", tier=UniverseTier.T2),
        ]
    )

    assert [s.symbol for s in ordered] == ["A", "B", "C"]


def test_symbol_is_the_final_deterministic_tie_break() -> None:
    """§9.2(5): "no randomness anywhere"."""
    ordered = rank([setup("ZECUSDT"), setup("ADAUSDT"), setup("MKRUSDT")])

    assert [s.symbol for s in ordered] == ["ADAUSDT", "MKRUSDT", "ZECUSDT"]


def test_a_shuffled_universe_ranks_identically() -> None:
    """S8's DoD: "identical inputs across shuffled symbol order -> identical ranking".

    This is the property a partial key set breaks without ever failing a
    single-case test: Python's sort is stable, so any pair the keys do not
    separate keeps its arrival order and looks decided. Twenty shuffles of a
    set built to collide on every key except the last is what catches it.
    """
    setups = [
        setup(f"SYM{i:02d}USDT", confidence=str(80 - i % 3), tier=UniverseTier.T1)
        for i in range(12)
    ]

    expected = [s.symbol for s in rank(setups)]

    shuffler = random.Random(20260823)

    for _ in range(20):
        shuffled = list(setups)
        shuffler.shuffle(shuffled)

        assert [s.symbol for s in rank(shuffled)] == expected


def test_direction_settles_a_tie_9_2_leaves_open() -> None:
    """§9.2's chain ends at the symbol, and two signals can share one.

    A long and a short on one symbol with equal confidence, archetype, TF and
    tier tie on every key the section states, and `sorted` would then fall
    back to arrival order -- which S8's DoD forbids. Direction decides it.
    """
    a = [setup("AAA", direction="UP"), setup("AAA", direction="DOWN")]

    assert [s.direction for s in rank(a)] == ["DOWN", "UP"]
    assert [s.direction for s in rank(list(reversed(a)))] == ["DOWN", "UP"]
