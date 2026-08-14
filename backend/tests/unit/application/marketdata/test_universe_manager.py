"""Unit tests for Sprint S3 universe manager orchestration."""

from __future__ import annotations

from decimal import Decimal

import pytest

from scanner.application.marketdata.universe import LiquiditySnapshot
from scanner.application.marketdata.universe_manager import UniverseManager
from scanner.application.ports import UniverseStateRecord
from scanner.domain.common.universe import UniverseTier


class FakeSymbolRepository:
    def __init__(
        self,
        state: UniverseStateRecord | None,
    ) -> None:
        self.state = state
        self.saved: list[UniverseStateRecord] = []

    async def get_universe_state(
        self,
        exchange_symbol: str,
    ) -> UniverseStateRecord | None:
        if self.state is None:
            return None

        if self.state.exchange_symbol != exchange_symbol:
            return None

        return self.state

    async def save_universe_state(
        self,
        state: UniverseStateRecord,
    ) -> None:
        self.saved.append(state)
        self.state = state


def snapshot(
    *,
    volume: str,
    spread: str,
    depth: str,
) -> LiquiditySnapshot:
    return LiquiditySnapshot(
        median_daily_quote_volume=Decimal(volume),
        median_spread_bps=Decimal(spread),
        median_depth_2pct=Decimal(depth),
    )


@pytest.mark.asyncio
async def test_unknown_symbol_raises_lookup_error() -> None:
    manager = UniverseManager(FakeSymbolRepository(None))

    with pytest.raises(
        LookupError,
        match="Unknown symbol: BTCUSDT",
    ):
        await manager.evaluate(
            "BTCUSDT",
            snapshot(
                volume="100000000",
                spread="2",
                depth="1000000",
            ),
        )


@pytest.mark.asyncio
async def test_first_t1_pass_starts_promotion_streak() -> None:
    repo = FakeSymbolRepository(
        UniverseStateRecord(
            exchange_symbol="BTCUSDT",
            tier=UniverseTier.INELIGIBLE,
        )
    )

    manager = UniverseManager(repo)

    report = await manager.evaluate(
        "BTCUSDT",
        snapshot(
            volume="100000000",
            spread="2",
            depth="1000000",
        ),
    )

    assert report.observed_tier is UniverseTier.T1
    assert report.previous_tier is UniverseTier.INELIGIBLE
    assert report.current_tier is UniverseTier.INELIGIBLE
    assert report.candidate_tier is UniverseTier.T1
    assert report.consecutive_passes == 1
    assert report.consecutive_failures == 0
    assert report.tier_changed is False

    assert repo.saved == [
        UniverseStateRecord(
            exchange_symbol="BTCUSDT",
            tier=UniverseTier.INELIGIBLE,
            candidate_tier=UniverseTier.T1,
            consecutive_passes=1,
            consecutive_failures=0,
        )
    ]


@pytest.mark.asyncio
async def test_seventh_t1_pass_promotes_symbol() -> None:
    repo = FakeSymbolRepository(
        UniverseStateRecord(
            exchange_symbol="BTCUSDT",
            tier=UniverseTier.INELIGIBLE,
            candidate_tier=UniverseTier.T1,
            consecutive_passes=6,
        )
    )

    manager = UniverseManager(repo)

    report = await manager.evaluate(
        "BTCUSDT",
        snapshot(
            volume="150000000",
            spread="1",
            depth="2000000",
        ),
    )

    assert report.observed_tier is UniverseTier.T1
    assert report.previous_tier is UniverseTier.INELIGIBLE
    assert report.current_tier is UniverseTier.T1
    assert report.candidate_tier is None
    assert report.consecutive_passes == 0
    assert report.consecutive_failures == 0
    assert report.tier_changed is True

    assert repo.saved[-1].tier is UniverseTier.T1


@pytest.mark.asyncio
async def test_first_t2_failure_starts_demotion_streak() -> None:
    repo = FakeSymbolRepository(
        UniverseStateRecord(
            exchange_symbol="BTCUSDT",
            tier=UniverseTier.T1,
        )
    )

    manager = UniverseManager(repo)

    report = await manager.evaluate(
        "BTCUSDT",
        snapshot(
            volume="50000000",
            spread="4",
            depth="500000",
        ),
    )

    assert report.observed_tier is UniverseTier.T2
    assert report.current_tier is UniverseTier.T1
    assert report.candidate_tier is UniverseTier.T2
    assert report.consecutive_failures == 1
    assert report.tier_changed is False


@pytest.mark.asyncio
async def test_third_t2_failure_demotes_symbol() -> None:
    repo = FakeSymbolRepository(
        UniverseStateRecord(
            exchange_symbol="BTCUSDT",
            tier=UniverseTier.T1,
            candidate_tier=UniverseTier.T2,
            consecutive_failures=2,
        )
    )

    manager = UniverseManager(repo)

    report = await manager.evaluate(
        "BTCUSDT",
        snapshot(
            volume="50000000",
            spread="4",
            depth="500000",
        ),
    )

    assert report.observed_tier is UniverseTier.T2
    assert report.previous_tier is UniverseTier.T1
    assert report.current_tier is UniverseTier.T2
    assert report.candidate_tier is None
    assert report.consecutive_passes == 0
    assert report.consecutive_failures == 0
    assert report.tier_changed is True

    assert repo.saved[-1].tier is UniverseTier.T2


@pytest.mark.asyncio
async def test_matching_tier_resets_existing_candidate_streak() -> None:
    repo = FakeSymbolRepository(
        UniverseStateRecord(
            exchange_symbol="BTCUSDT",
            tier=UniverseTier.T2,
            candidate_tier=UniverseTier.T1,
            consecutive_passes=4,
        )
    )

    manager = UniverseManager(repo)

    report = await manager.evaluate(
        "BTCUSDT",
        snapshot(
            volume="50000000",
            spread="4",
            depth="500000",
        ),
    )

    assert report.observed_tier is UniverseTier.T2
    assert report.current_tier is UniverseTier.T2
    assert report.candidate_tier is None
    assert report.consecutive_passes == 0
    assert report.consecutive_failures == 0
    assert report.tier_changed is False


@pytest.mark.asyncio
async def test_ineligible_observation_starts_demotion_from_t3() -> None:
    repo = FakeSymbolRepository(
        UniverseStateRecord(
            exchange_symbol="BTCUSDT",
            tier=UniverseTier.T3,
        )
    )

    manager = UniverseManager(repo)

    report = await manager.evaluate(
        "BTCUSDT",
        snapshot(
            volume="4000000",
            spread="8",
            depth="120000",
        ),
    )

    assert report.observed_tier is UniverseTier.INELIGIBLE
    assert report.current_tier is UniverseTier.T3
    assert report.candidate_tier is UniverseTier.INELIGIBLE
    assert report.consecutive_failures == 1
