"""§9.2's cross-symbol ordering — five keys, no randomness anywhere."""

from __future__ import annotations

from collections.abc import Iterable, Sequence

from scanner.domain.confluence import ranking_priority
from scanner.domain.ranking.model import RankableSetup, tier_priority


def _key(setup: RankableSetup) -> tuple[object, ...]:
    """§9.2's ordering, in the order the section states it.

    "Published signals rank by: (1) FinalConfidence (desc); (2) tie-break:
    archetype priority A1 > A2 > A5 > A3 > A4; (3) tie-break: higher TF;
    (4) tie-break: higher liquidity tier; (5) final deterministic tie-break:
    symbol lexicographic. No randomness anywhere."

    Confidence and timeframe are negated rather than reverse-sorted, because
    the five keys point in different directions and one `reverse=True` would
    flip all of them. `timeframe.minutes` is what "higher TF" means -- a
    ladder position, and the enum's declaration order is not one.

    The last key is what makes this a *total* order. Two setups on the same
    symbol, timeframe, archetype and confidence would still be one setup; with
    the symbol tie-break the sort is deterministic even so, which is what
    "no randomness anywhere" has to mean in practice: the same inputs in a
    different arrival order produce the same list.
    """
    return (
        -setup.confidence,
        ranking_priority(setup.archetype),
        -setup.timeframe.minutes,
        tier_priority(setup.tier),
        setup.symbol,
        # Past the end of §9.2's chain -- see `RankableSetup.direction`.
        setup.direction,
    )


def rank(setups: Iterable[RankableSetup]) -> tuple[RankableSetup, ...]:
    """Order published setups for the market-wide board.

    Input order does not reach the output: every key is a property of the
    setup itself, so a shuffled universe ranks identically. S8's DoD asks for
    exactly that ("identical inputs across shuffled symbol order -> identical
    ranking"), and it is a property the sort cannot have by accident -- a
    partial key set would let Python's stable sort leak arrival order into the
    result and look correct on any single run.
    """
    return tuple(sorted(setups, key=_key))


def rank_positions(setups: Sequence[RankableSetup]) -> dict[tuple[str, str], int]:
    """1-based board position, keyed by (symbol, direction).

    Keyed on both because one symbol can carry a long and a short candidate at
    the same close, and §9.2 ranks *published signals* rather than symbols.
    Keying on the symbol alone would have silently dropped one of the pair.
    """
    ordered = rank(setups)
    keys = [(setup.symbol, setup.direction) for setup in ordered]

    if len(set(keys)) != len(keys):
        raise ValueError("two signals with one (symbol, direction) at one close")

    return {key: position for position, key in enumerate(keys, start=1)}
