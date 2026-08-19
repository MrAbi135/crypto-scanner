"""EQH/EQL clustering tests (SLS §4.3)."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from scanner.domain.liquidity import (
    EqualLevelCluster,
    LiquidityClass,
    LiquiditySide,
    PoolSource,
    detect_equal_level_clusters,
    pool_from_cluster,
)
from scanner.domain.structure import (
    SwingKind,
    SwingPoint,
    SwingStrength,
    swing_window,
)

# INTERNAL swings confirm k candles after the extreme, so a swing at index 8
# is confirmed some way past it; the ATR series has to be long enough to
# reach that candle or the pair is simply never evaluated.
SERIES_LEN = 60


def flat_atr(value: str = "2", *, length: int = SERIES_LEN) -> tuple[Decimal | None, ...]:
    """A constant ATR, so a test's arithmetic is the only thing moving."""
    return tuple(Decimal(value) for _ in range(length))


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

    clusters = detect_equal_level_clusters(swings, atrs=flat_atr())

    assert len(clusters) == 1
    assert clusters[0].side is LiquiditySide.BSL
    assert clusters[0].member_count == 2


def test_flat_shelf_is_not_cluster() -> None:
    swings = (
        make_swing(2, "100", SwingKind.HIGH),
        make_swing(5, "99.8", SwingKind.LOW),
        make_swing(8, "100.02", SwingKind.HIGH),
    )

    clusters = detect_equal_level_clusters(swings, atrs=flat_atr())

    assert clusters == ()


def test_epsilon_is_measured_against_the_atr_at_confirmation() -> None:
    """The point of taking a series (§4.3).

    The same two highs are 0.02 apart. At ATR 2 that is inside
    `epsilon = 0.05 x ATR = 0.1` and they cluster; at ATR 0.2 epsilon is 0.01
    and they do not. Reading ATR from wherever the replay window happened to
    end would let the same history cluster on one run and not the next, so a
    stored cluster pool could not be re-derived -- which §4.2 requires.
    """
    swings = (
        make_swing(2, "100", SwingKind.HIGH),
        make_swing(5, "98", SwingKind.LOW),
        make_swing(8, "100.02", SwingKind.HIGH),
    )

    assert detect_equal_level_clusters(swings, atrs=flat_atr("2"))
    assert detect_equal_level_clusters(swings, atrs=flat_atr("0.2")) == ()


def test_a_pair_whose_confirmation_has_no_atr_is_not_evaluated() -> None:
    """Guessing an ATR in the seeding region would invent the threshold."""
    swings = (
        make_swing(2, "100", SwingKind.HIGH),
        make_swing(5, "98", SwingKind.LOW),
        make_swing(8, "100.02", SwingKind.HIGH),
    )

    assert detect_equal_level_clusters(swings, atrs=(None,) * SERIES_LEN) == ()


def test_a_confirmation_past_the_end_of_the_series_is_not_evaluated() -> None:
    swings = (
        make_swing(2, "100", SwingKind.HIGH),
        make_swing(5, "98", SwingKind.LOW),
        make_swing(8, "100.02", SwingKind.HIGH),
    )

    assert detect_equal_level_clusters(swings, atrs=flat_atr(length=9)) == ()


def test_the_cluster_is_stamped_at_its_second_member_confirmation() -> None:
    """§4.3: "cluster confirmed when its second member swing confirms".

    Third and later members append, so the stamp must not move with them --
    otherwise a cluster's age, and therefore its §4.2 age component, would
    reset every time it grew.
    """
    swings = (
        make_swing(2, "100", SwingKind.HIGH),
        make_swing(5, "98", SwingKind.LOW),
        make_swing(8, "100.02", SwingKind.HIGH),
        make_swing(11, "98.1", SwingKind.LOW),
        make_swing(14, "100.01", SwingKind.HIGH),
    )

    clusters = detect_equal_level_clusters(swings, atrs=flat_atr())

    cluster = next(c for c in clusters if c.side is LiquiditySide.BSL)

    assert cluster.member_count == 3
    assert cluster.confirmed_index == 8 + swing_window(SwingStrength.INTERNAL)


def test_a_chain_band_may_exceed_epsilon_across_its_ends() -> None:
    """§4.3 edge case 1: membership is pairwise-adjacent, band is full min/max.

    The outer pair here differs by more than epsilon (0.1 at ATR 2) while each
    adjacent pair passes, so the chain holds and the band spans all three.
    """
    swings = (
        make_swing(2, "100", SwingKind.HIGH),
        make_swing(5, "98", SwingKind.LOW),
        make_swing(8, "100.08", SwingKind.HIGH),
        make_swing(11, "98.1", SwingKind.LOW),
        make_swing(14, "100.16", SwingKind.HIGH),
    )

    cluster = next(
        c
        for c in detect_equal_level_clusters(swings, atrs=flat_atr())
        if c.side is LiquiditySide.BSL
    )

    assert cluster.member_count == 3
    assert cluster.band_high - cluster.band_low > Decimal("0.1")


def test_equal_lows_mirror_equal_highs() -> None:
    swings = (
        make_swing(2, "100", SwingKind.LOW),
        make_swing(5, "102", SwingKind.HIGH),
        make_swing(8, "100.02", SwingKind.LOW),
    )

    clusters = detect_equal_level_clusters(swings, atrs=flat_atr())

    assert len(clusters) == 1
    assert clusters[0].side is LiquiditySide.SSL
    assert clusters[0].extreme == Decimal("100")


def test_a_bsl_clusters_price_is_its_highest_member() -> None:
    """§4.2: a cluster pool's price is the extreme of its members."""
    swings = (
        make_swing(2, "100", SwingKind.HIGH),
        make_swing(5, "98", SwingKind.LOW),
        make_swing(8, "100.02", SwingKind.HIGH),
    )

    cluster = detect_equal_level_clusters(swings, atrs=flat_atr())[0]

    assert cluster.extreme == Decimal("100.02")


def test_members_closer_than_the_minimum_gap_are_skipped() -> None:
    swings = (
        make_swing(2, "100", SwingKind.HIGH),
        make_swing(3, "100.01", SwingKind.HIGH),
    )

    assert detect_equal_level_clusters(swings, atrs=flat_atr()) == ()


def test_members_further_apart_than_the_maximum_gap_do_not_cluster() -> None:
    swings = (
        make_swing(2, "100", SwingKind.HIGH),
        make_swing(5, "98", SwingKind.LOW),
        make_swing(50, "100.02", SwingKind.HIGH),
    )

    assert detect_equal_level_clusters(swings, atrs=flat_atr(), max_gap=10) == ()


class TestPoolFromCluster:
    """§4.2(b) — the constructor that unpins `cluster_factor`."""

    @staticmethod
    def _cluster(members: int) -> EqualLevelCluster:
        prices = tuple(Decimal("100") + Decimal("0.01") * i for i in range(members))

        return EqualLevelCluster(
            cluster_id="BSL:2:8",
            side=LiquiditySide.BSL,
            member_indices=tuple(2 + 3 * i for i in range(members)),
            member_prices=prices,
            band_low=min(prices),
            band_high=max(prices),
            confirmed_index=10,
        )

    @staticmethod
    def _build(cluster: EqualLevelCluster):
        return pool_from_cluster(
            cluster,
            pool_id="p1",
            liquidity_class=LiquidityClass.EXTERNAL,
            created_at=datetime(2026, 8, 15, tzinfo=UTC),
            touches=0,
            timeframe_rank=1,
            max_timeframe_rank=8,
            age_candles=0,
        )

    def test_cluster_size_moves_the_strength_component(self) -> None:
        """The defect this exists to end.

        Every production pool was built with `member_count=1`, so
        `cluster_component` was 6.25 on all of them -- 25 of the 100 points
        could not vary. §4.2 grades 2-member clusters at 0.5 and 3+ at 1.0.
        """
        two = self._build(self._cluster(2)).strength.cluster_component
        three = self._build(self._cluster(3)).strength.cluster_component

        assert two == Decimal("12.5")
        assert three == Decimal("25")
        assert two > Decimal("6.25")

    def test_price_is_the_extreme_not_the_band_midpoint(self) -> None:
        """§4.2 keeps the band separately "for sweep tolerance".

        A midpoint price sits inside the band, so a sweep that genuinely took
        the cluster's stops could close back through it and read as no sweep.
        """
        pool = self._build(self._cluster(3))

        assert pool.price == pool.band_high
        assert pool.band_low < pool.band_high

    def test_the_pool_is_stamped_at_the_clusters_confirmation(self) -> None:
        pool = self._build(self._cluster(2))

        assert pool.created_index == 10
        assert pool.source is PoolSource.CLUSTER

    def test_an_ssl_cluster_prices_at_its_lowest_member(self) -> None:
        base = self._cluster(3)

        pool = self._build(
            EqualLevelCluster(
                cluster_id=base.cluster_id,
                side=LiquiditySide.SSL,
                member_indices=base.member_indices,
                member_prices=base.member_prices,
                band_low=base.band_low,
                band_high=base.band_high,
                confirmed_index=base.confirmed_index,
            )
        )

        assert pool.price == pool.band_low
