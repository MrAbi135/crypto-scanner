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


def rank_positions(setups: Sequence[RankableSetup]) -> dict[str, int]:
    """1-based board position per symbol, for the ranked setups given.

    Keyed by symbol because §9.2 ranks *published signals* market-wide and one
    symbol publishes at most one; if that stops being true this needs a
    composite key, and the assertion below is what will say so.
    """
    ordered = rank(setups)
    symbols = [setup.symbol for setup in ordered]

    if len(set(symbols)) != len(symbols):
        raise ValueError("two setups on one symbol: §9.2 positions are per symbol")

    return {symbol: position for position, symbol in enumerate(symbols, start=1)}
