"""Liquidity reads are pinned to the running algo version.

A version bump re-hashes every pool id, so for one window after a deploy the
pool table and the transition ledger hold two generations of the same levels.
Measured on the host after the s5-v11 deploy (2026-09-13): the new engine had
swept and matured the previous generation's pools under its own label -- 124
stop hunts and 1,596 reclaims about s5-v10 sweeps, still carrying the class
s5-v11 existed to correct -- and every consumer read both generations. The
same had happened on the s5-v7 -> s5-v10 bump. Zones got this pin in PR #191;
liquidity never did.

These run the real replay against the golden harness's in-memory stores, which
resolve a transition's version through its pool exactly as the SQL join does,
so deleting any pin in `LiquidityReplayService` fails one of them.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from tests.golden.harness.memory import (
    InMemoryEngineEventRepository,
    InMemoryIctEvidenceRepository,
    InMemoryLiquidityPoolRepository,
    InMemoryLiquidityStateStore,
    InMemoryLiquidityTransitionRepository,
    pool_version,
)
from tests.support.builders import pad_for_warmup

from scanner.application.detection.liquidity_replay import (
    LIQUIDITY_ALGO_VERSION,
    LiquidityReplayService,
)
from scanner.application.ports.liquidity_detection import (
    LiquidityPoolRecord,
    LiquidityTransitionRecord,
)
from scanner.domain.common import Candle, CandleSource
from scanner.shared import Timeframe

TF = Timeframe.M5
SYMBOL = "BTCUSDT"
BASE = datetime(2026, 8, 15, 10, 0, tzinfo=UTC)
PREVIOUS = "s5-previous"


class FixedClock:
    def now(self) -> datetime:
        return datetime(2026, 8, 16, 12, 0, tzinfo=UTC)


def bar(index: int, *, open_: str, high: str, low: str, close: str) -> Candle:
    return Candle(
        symbol=SYMBOL,
        timeframe=TF,
        open_time=BASE + TF.duration * index,
        open=Decimal(open_),
        high=Decimal(high),
        low=Decimal(low),
        close=Decimal(close),
        volume=Decimal("100"),
        quote_volume=Decimal("10000"),
        taker_buy_volume=Decimal("50"),
        trade_count=10,
        source=CandleSource.BACKFILL,
    )


def pool(
    pool_id: str,
    *,
    version: str,
    created_at: datetime,
    price: str = "100",
    state: str = "ACTIVE",
) -> LiquidityPoolRecord:
    level = Decimal(price)

    return LiquidityPoolRecord(
        pool_id=pool_id,
        symbol=SYMBOL,
        timeframe=TF,
        side="BSL",
        liquidity_class="EXTERNAL",
        source="SWING",
        price=level,
        band_low=level,
        band_high=level,
        strength=Decimal("50"),
        state=state,
        member_count=1,
        created_index=0,
        created_at=created_at,
        updated_at=created_at,
        evidence=json.dumps({"algo_version": version, "swing_strength": "EXTERNAL"}),
    )


class Stores:
    def __init__(self) -> None:
        self.pools = InMemoryLiquidityPoolRepository()
        self.transitions = InMemoryLiquidityTransitionRepository()
        self.events = InMemoryEngineEventRepository()
        self.evidence = InMemoryIctEvidenceRepository(self.events, self.transitions, self.pools)
        self.snapshots = InMemoryLiquidityStateStore()

    def service(self, candles: list[Candle]) -> LiquidityReplayService:
        from tests.golden.harness.memory import InMemoryCandleRepository

        return LiquidityReplayService(
            InMemoryCandleRepository(candles),
            self.pools,
            self.transitions,
            self.events,
            self.snapshots,
            self.evidence,
            FixedClock(),
        )

    def facts_about(self, pool_id: str) -> list[str]:
        found = []

        for event in self.events.events:
            payload = json.loads(event.payload)

            if pool_id in (payload.get("pool_id"), payload.get("sweep_pool_id")):
                found.append(event.event_type)

        return found


async def run(stores: Stores, candles: list[Candle]) -> None:
    await stores.service(candles).run(
        SYMBOL, TF, candles[0].open_time, candles[-1].open_time + TF.duration
    )


def sweep_series() -> list[Candle]:
    """The last candle wicks to 102 through a level at 100 and closes back at 99."""

    return pad_for_warmup(
        [
            bar(0, open_="97", high="99", low="97", close="98"),
            bar(1, open_="99", high="102", low="98", close="99"),
        ]
    )


@pytest.mark.asyncio
async def test_another_versions_pool_is_not_swept_by_this_version() -> None:
    """Two generations of one level at 100. The candle sweeps it: this
    version's pool records the sweep, the previous version's pool is left
    alone -- no transition, no fact under this version's label."""

    candles = sweep_series()
    stores = Stores()
    created = candles[-3].close_time

    await stores.pools.upsert(pool("p-current", version=LIQUIDITY_ALGO_VERSION, created_at=created))
    await stores.pools.upsert(pool("p-previous", version=PREVIOUS, created_at=created))

    await run(stores, candles)

    # The premise: the candle does sweep a level at 100.
    assert stores.pools.pools["p-current"].state == "SWEPT"
    assert "LIQUIDITY_SWEEP" in stores.facts_about("p-current")

    assert stores.pools.pools["p-previous"].state == "ACTIVE"
    assert stores.facts_about("p-previous") == []
    assert not [t for t in stores.transitions.transitions if t.pool_id == "p-previous"]

    # Nor is it this version's resting liquidity: the snapshot the API and the
    # target term read holds one generation.
    resting = stores.snapshots.snapshots[(SYMBOL, TF)]
    assert "p-previous" not in {record.pool_id for record in resting}


@pytest.mark.asyncio
async def test_another_versions_pool_is_retired_by_age_and_nothing_else() -> None:
    """Left ACTIVE forever, a superseded generation would never leave the
    table. Once it is older than §4.2's 500 candles it expires -- as a
    transition, with no event -- the one thing this version does to it."""

    candles = sweep_series()
    stores = Stores()
    aged = candles[-1].close_time - TF.duration * 501

    await stores.pools.upsert(pool("p-aged", version=PREVIOUS, created_at=aged, price="500"))

    await run(stores, candles)

    assert stores.pools.pools["p-aged"].state == "EXPIRED"
    assert [t.reason for t in stores.transitions.transitions if t.pool_id == "p-aged"] == [
        "pool_max_age"
    ]
    assert stores.facts_about("p-aged") == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("version", "matures"),
    [(LIQUIDITY_ALGO_VERSION, True), (PREVIOUS, False)],
)
async def test_only_this_versions_sweeps_mature(version: str, matures: bool) -> None:
    """A sweep recorded on an earlier pass, then a close back above its level
    two candles later: §4.6's reclaim. Published for this version's sweep --
    which proves the fixture reclaims -- and not for the previous version's."""

    candles = pad_for_warmup(
        [
            bar(0, open_="97", high="99", low="97", close="98"),
            bar(1, open_="99", high="102", low="98", close="99"),
            bar(2, open_="99", high="100.5", low="98", close="99.5"),
            bar(3, open_="99.5", high="101", low="99", close="100.5"),
        ]
    )
    stores = Stores()
    swept_at = candles[-3].close_time

    await stores.pools.upsert(
        pool("p-swept", version=version, created_at=candles[-5].close_time, state="SWEPT")
    )
    await stores.transitions.append(
        LiquidityTransitionRecord(
            transition_id="t-swept",
            pool_id="p-swept",
            symbol=SYMBOL,
            timeframe=TF,
            from_state="ACTIVE",
            to_state="SWEPT",
            reason="liquidity_sweep",
            transitioned_at=swept_at,
            candle_index=497,
            evidence=json.dumps(
                {
                    "pool_id": "p-swept",
                    "side": "BSL",
                    "liquidity_class": "EXTERNAL",
                    "reference_level": "100",
                    "penetration_price": "102",
                    "close_back_price": "99",
                    "sweep_depth_atr": "1.2",
                    "confirmation_window": 1,
                    "gap_sweep": False,
                    "reclaimed": False,
                    "displaced_after": False,
                    "setup_expiry_index": 512,
                }
            ),
        )
    )

    await run(stores, candles)

    assert ("LIQUIDITY_SWEEP_RECLAIMED" in stores.facts_about("p-swept")) is matures


@pytest.mark.asyncio
async def test_a_level_held_from_before_the_window_absorbs_a_swing_inside_it() -> None:
    """The window may have cut a pool's pivot off, but the pool still holds its
    level: the map walk reads this version's pools from before the window back
    from persistence, so a swing at the same price inside it is not a second
    pool on one level (§4.2)."""

    candles = pad_for_warmup(
        [
            bar(0, open_="98", high="99", low="97", close="98"),
            bar(1, open_="99", high="100", low="98", close="99"),
            bar(2, open_="100", high="101", low="99", close="100"),
            bar(3, open_="99.5", high="100", low="98.5", close="99"),
            bar(4, open_="99", high="99.5", low="98", close="98.5"),
        ]
    )
    stores = Stores()

    await stores.pools.upsert(
        pool(
            "p-held",
            version=LIQUIDITY_ALGO_VERSION,
            created_at=candles[0].close_time - TF.duration,
            price="101",
        )
    )

    await run(stores, candles)

    at_level = [
        record.pool_id for record in stores.pools.pools.values() if record.price == Decimal("101")
    ]

    assert at_level == ["p-held"]


@pytest.mark.asyncio
async def test_a_consumed_level_does_not_absorb_a_later_swing_at_its_price() -> None:
    """A pool swept before a new swing confirms at its price no longer holds
    the level: the swing is new liquidity and becomes a pool of its own. Held
    against the pools alive now instead of on the swing's candle, whichever
    pass ran first decided it (audit M10)."""

    candles = pad_for_warmup(
        [
            bar(0, open_="98", high="99", low="97", close="98"),
            bar(1, open_="99", high="100", low="98", close="99"),
            bar(2, open_="100", high="101", low="99", close="100"),
            bar(3, open_="99.5", high="100", low="98.5", close="99"),
            bar(4, open_="99", high="99.5", low="98", close="98.5"),
        ]
    )
    stores = Stores()
    swept_at = candles[-20].close_time

    await stores.pools.upsert(
        pool(
            "p-taken",
            version=LIQUIDITY_ALGO_VERSION,
            created_at=candles[0].close_time - TF.duration,
            price="101",
            state="SWEPT",
        )
    )
    await stores.transitions.append(
        LiquidityTransitionRecord(
            transition_id="t-taken",
            pool_id="p-taken",
            symbol=SYMBOL,
            timeframe=TF,
            from_state="ACTIVE",
            to_state="SWEPT",
            reason="liquidity_sweep",
            transitioned_at=swept_at,
            candle_index=len(candles) - 20,
            evidence="{}",
        )
    )

    await run(stores, candles)

    at_level = sorted(
        record.pool_id for record in stores.pools.pools.values() if record.price == Decimal("101")
    )

    assert len(at_level) == 2 and "p-taken" in at_level, at_level


@pytest.mark.asyncio
async def test_no_pool_is_born_on_a_candle_an_earlier_pass_decided() -> None:
    """Audit M3 (owner ruling 2026-09-14): epsilon is zero on a window's first
    candles (Wilder ATR has no value there), so a level another pool held while
    it sat deeper in the window became its own pool once it reached them, and
    that pass wrote its old sweep. A pass births no pool on a candle an earlier
    pass decided and wrote no row for; the same window undecided births it."""
    from tests.golden.harness.memory import InMemoryCandleRepository, InMemoryEngineStateStore

    from scanner.application.detection.state import (
        LIQUIDITY_NAMESPACE,
        EngineStateManager,
        StructureEngineState,
    )

    candles = pad_for_warmup(
        [
            bar(0, open_="98", high="99", low="97", close="98"),
            bar(1, open_="99", high="100", low="98", close="99"),
            bar(2, open_="100", high="101", low="99", close="100"),
            bar(3, open_="99.5", high="100", low="98.5", close="99"),
            bar(4, open_="99", high="99.5", low="98", close="98.5"),
        ]
    )

    async def pools_at_101(state: EngineStateManager | None) -> list[str]:
        stores = Stores()
        await LiquidityReplayService(
            InMemoryCandleRepository(candles),
            stores.pools,
            stores.transitions,
            stores.events,
            stores.snapshots,
            stores.evidence,
            FixedClock(),
            state=state,
        ).run(SYMBOL, TF, candles[0].open_time, candles[-1].open_time + TF.duration)
        return [p.pool_id for p in stores.pools.pools.values() if p.price == Decimal("101")]

    decided = EngineStateManager(InMemoryEngineStateStore(), namespace=LIQUIDITY_NAMESPACE)
    await decided.save(
        StructureEngineState(
            symbol=SYMBOL,
            timeframe=TF.value,
            algo_version=LIQUIDITY_ALGO_VERSION,
            last_processed_open_time=candles[-1].open_time.isoformat(),
        )
    )

    # The premise: undecided, the window births the pool at the 101 swing high.
    assert await pools_at_101(None)
    assert await pools_at_101(decided) == []


@pytest.mark.asyncio
async def test_a_level_held_by_another_version_does_not_absorb_this_versions_pool() -> None:
    """§4.2's dedup asks whether a level is already this map's. A previous
    version's pool at the same price is not: absorbed into it, this version
    would have no pool of its own there, and its sweep would be recorded
    against the old generation's."""

    candles = pad_for_warmup(
        [
            bar(0, open_="98", high="99", low="97", close="98"),
            bar(1, open_="99", high="100", low="98", close="99"),
            # A pivot high at 101, confirmed by two lower highs after it.
            bar(2, open_="100", high="101", low="99", close="100"),
            bar(3, open_="99.5", high="100", low="98.5", close="99"),
            bar(4, open_="99", high="99.5", low="98", close="98.5"),
        ]
    )
    stores = Stores()

    # Held since before this window, so the map walk reads it back from
    # persistence -- the read the version pin is on. (A pool confirmed inside
    # the window is rebuilt by the walk and never read back at all.)
    await stores.pools.upsert(
        pool(
            "p-previous",
            version=PREVIOUS,
            created_at=candles[0].close_time - TF.duration,
            price="101",
        )
    )

    await run(stores, candles)

    mine = [
        record
        for record in stores.pools.pools.values()
        if pool_version(record) == LIQUIDITY_ALGO_VERSION and record.price == Decimal("101")
    ]

    assert mine, "this version built no pool at the 101 swing high"
    assert stores.pools.pools["p-previous"].state == "ACTIVE"
