"""EQH/EQL clustering (SLS §4.3).

## Why this takes an ATR series rather than one ATR

`epsilon = 0.05 x ATR` decides membership, and membership decides `member_count`,
which is a quarter of §4.2's pool strength. So the ATR chosen here is not a
detail — it moves a score.

A single ATR for the whole call has to come from somewhere, and the only
available "somewhere" in a replay is the end of the window. That makes ε slide
with the window: the same two historical swing highs cluster on one run and not
the next, so a cluster pool persisted yesterday cannot be re-derived today.
§4.2 requires the opposite — *"every component is recomputable from stored
evidence"*.

§4.3 says a cluster is confirmed *"when its second member swing confirms"*.
That is a specific candle, and its ATR is the one the doctrine means. Passing
the series lets each pair be judged at its own confirmation, which is both
faithful and stable under replay.
"""

from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal

from scanner.domain.liquidity.model import (
    EqualLevelCluster,
    LiquiditySide,
)
from scanner.domain.structure import SwingKind, SwingPoint, swing_window

EPSILON_ATR = Decimal("0.05")


def detect_equal_level_clusters(
    swings: Sequence[SwingPoint],
    *,
    atrs: Sequence[Decimal | None],
    min_gap: int = 3,
    max_gap: int = 100,
    min_depth_atr: Decimal = Decimal("0.5"),
) -> tuple[EqualLevelCluster, ...]:
    """Detect deterministic EQH/EQL chains from confirmed swings.

    `atrs` is Wilder ATR indexed by candle index (`wilder_atr_series`). A pair
    whose confirmation falls in the seeding region, or past the end of the
    series, is not evaluated: §1.9's warm-up gate keeps production clear of it,
    and guessing an ATR there would invent the threshold.
    """

    if min_gap < 1:
        raise ValueError("min_gap must be positive")

    if max_gap < min_gap:
        raise ValueError("max_gap must be >= min_gap")

    if min_depth_atr < 0:
        raise ValueError("min_depth_atr must be non-negative")

    highs = sorted(
        (swing for swing in swings if swing.kind is SwingKind.HIGH),
        key=lambda item: item.index,
    )

    lows = sorted(
        (swing for swing in swings if swing.kind is SwingKind.LOW),
        key=lambda item: item.index,
    )

    clusters: list[EqualLevelCluster] = []

    clusters.extend(
        _cluster_side(
            highs,
            opposite=lows,
            side=LiquiditySide.BSL,
            atrs=atrs,
            min_gap=min_gap,
            max_gap=max_gap,
            min_depth_atr=min_depth_atr,
        )
    )

    clusters.extend(
        _cluster_side(
            lows,
            opposite=highs,
            side=LiquiditySide.SSL,
            atrs=atrs,
            min_gap=min_gap,
            max_gap=max_gap,
            min_depth_atr=min_depth_atr,
        )
    )

    clusters.sort(
        key=lambda cluster: (
            cluster.member_indices[0],
            cluster.side.value,
        )
    )

    return tuple(clusters)


def confirmation_index(swing: SwingPoint) -> int:
    """The candle at which `swing` is confirmed (§3.1's k-window)."""

    return swing.index + swing_window(swing.strength)


def _cluster_side(
    members: Sequence[SwingPoint],
    *,
    opposite: Sequence[SwingPoint],
    side: LiquiditySide,
    atrs: Sequence[Decimal | None],
    min_gap: int,
    max_gap: int,
    min_depth_atr: Decimal,
) -> list[EqualLevelCluster]:
    result: list[EqualLevelCluster] = []
    index = 0

    while index < len(members) - 1:
        chain = [members[index]]
        cursor = index + 1

        while cursor < len(members):
            previous = chain[-1]
            current = members[cursor]
            gap = current.index - previous.index

            if gap > max_gap:
                break

            if gap < min_gap:
                cursor += 1
                continue

            # The later member is the one whose confirmation makes the pair a
            # cluster (§4.3), so its ATR sets ε and the depth floor.
            atr = _atr_at_confirmation(current, atrs)

            if atr is None:
                break

            if abs(current.price - previous.price) > EPSILON_ATR * atr:
                break

            if not _has_required_depth(
                previous,
                current,
                opposite=opposite,
                side=side,
                atr=atr,
                min_depth_atr=min_depth_atr,
            ):
                break

            chain.append(current)
            cursor += 1

        if len(chain) >= 2:
            prices = tuple(item.price for item in chain)
            indices = tuple(item.index for item in chain)

            result.append(
                EqualLevelCluster(
                    cluster_id=(f"{side.value}:" + ":".join(str(value) for value in indices)),
                    side=side,
                    member_indices=indices,
                    member_prices=prices,
                    band_low=min(prices),
                    band_high=max(prices),
                    # §4.3: "cluster confirmed when its second member swing
                    # confirms". Later members join by append, so the birth
                    # stamp stays the second member's -- it does not move as
                    # the chain grows.
                    confirmed_index=confirmation_index(chain[1]),
                )
            )

            index += len(chain)
        else:
            index += 1

    return result


def _atr_at_confirmation(
    swing: SwingPoint,
    atrs: Sequence[Decimal | None],
) -> Decimal | None:
    at = confirmation_index(swing)

    if at >= len(atrs):
        return None

    atr = atrs[at]

    return atr if atr is not None and atr > 0 else None


def _has_required_depth(
    first: SwingPoint,
    second: SwingPoint,
    *,
    opposite: Sequence[SwingPoint],
    side: LiquiditySide,
    atr: Decimal,
    min_depth_atr: Decimal,
) -> bool:
    between = [swing for swing in opposite if first.index < swing.index < second.index]

    if not between:
        return False

    required_depth = min_depth_atr * atr

    if side is LiquiditySide.BSL:
        cluster_floor = min(first.price, second.price)
        deepest_low = min(swing.price for swing in between)

        return cluster_floor - deepest_low >= required_depth

    cluster_ceiling = max(first.price, second.price)
    highest_high = max(swing.price for swing in between)

    return highest_high - cluster_ceiling >= required_depth
