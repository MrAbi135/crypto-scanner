"""EQH/EQL clustering (SLS §4.3)."""

from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal

from scanner.domain.liquidity.model import (
    EqualLevelCluster,
    LiquiditySide,
)
from scanner.domain.structure import SwingKind, SwingPoint


def detect_equal_level_clusters(
    swings: Sequence[SwingPoint],
    *,
    atr: Decimal,
    min_gap: int = 3,
    max_gap: int = 100,
    min_depth_atr: Decimal = Decimal("0.5"),
) -> tuple[EqualLevelCluster, ...]:
    """Detect deterministic EQH/EQL chains from confirmed swings."""

    if atr <= 0:
        raise ValueError("atr must be positive")

    if min_gap < 1:
        raise ValueError("min_gap must be positive")

    if max_gap < min_gap:
        raise ValueError("max_gap must be >= min_gap")

    if min_depth_atr < 0:
        raise ValueError("min_depth_atr must be non-negative")

    epsilon = Decimal("0.05") * atr

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
            epsilon=epsilon,
            atr=atr,
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
            epsilon=epsilon,
            atr=atr,
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


def _cluster_side(
    members: Sequence[SwingPoint],
    *,
    opposite: Sequence[SwingPoint],
    side: LiquiditySide,
    epsilon: Decimal,
    atr: Decimal,
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

            if abs(current.price - previous.price) > epsilon:
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
                )
            )

            index += len(chain)
        else:
            index += 1

    return result


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
