"""Tests for SLS §5.9 uniform zone interaction replay."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from tests.support.builders import pad_for_warmup

import scanner.application.detection.ict_interaction_replay as interaction_module
from scanner.application.detection.ict_interaction_replay import (
    IctZoneInteractionReplayService,
)
from scanner.application.ports.ict_zone_interactions import (
    IctZoneInteractionRecord,
)
from scanner.application.ports.ict_zones import (
    IctZoneRecord,
    IctZoneTransitionRecord,
)
from scanner.domain.common import (
    Candle,
    CandleSource,
)
from scanner.domain.structure import (
    SwingKind,
    SwingPoint,
    SwingStrength,
)
from scanner.shared import Timeframe


class FakeCandleRepository:
    def __init__(
        self,
        candles: list[Candle],
    ) -> None:
        self.candles = candles

    async def fetch_series(
        self,
        symbol: str,
        timeframe: Timeframe,
        start: datetime,
        end: datetime,
    ) -> list[Candle]:
        return [
            candle
            for candle in self.candles
            if (
                candle.symbol == symbol
                and candle.timeframe is timeframe
                and start <= candle.open_time < end
            )
        ]


class FakeContextRepository:
    def __init__(
        self,
        zones: tuple[IctZoneRecord, ...],
        transitions: tuple[
            IctZoneTransitionRecord,
            ...,
        ] = (),
    ) -> None:
        self.zones = zones
        self.transitions = transitions

    async def list_zones(
        self,
        symbol: str,
        timeframe: Timeframe,
    ) -> tuple[IctZoneRecord, ...]:
        return tuple(
            zone for zone in self.zones if (zone.symbol == symbol and zone.timeframe is timeframe)
        )

    async def list_transitions(
        self,
        zone_id: str,
    ) -> tuple[
        IctZoneTransitionRecord,
        ...,
    ]:
        return tuple(transition for transition in self.transitions if transition.zone_id == zone_id)


class FakeInteractionRepository:
    def __init__(self) -> None:
        self.records: dict[
            str,
            IctZoneInteractionRecord,
        ] = {}

    async def append(
        self,
        interaction: IctZoneInteractionRecord,
    ) -> bool:
        if interaction.interaction_id in self.records:
            return False

        self.records[interaction.interaction_id] = interaction

        return True

    async def append_many(self, interactions) -> frozenset[str]:
        """Mirrors the SQL: ON CONFLICT DO NOTHING ... RETURNING id.

        Only the ids it actually wrote come back, so a second replay reports
        zero rather than re-counting rows that were already there.
        """
        written: set[str] = set()

        for interaction in interactions:
            if interaction.interaction_id in self.records:
                continue

            self.records[interaction.interaction_id] = interaction
            written.add(interaction.interaction_id)

        return frozenset(written)


def make_candle(
    *,
    symbol: str,
    timeframe: Timeframe,
    index: int,
    open_: str,
    high: str,
    low: str,
    close: str,
) -> Candle:
    return Candle(
        symbol=symbol,
        timeframe=timeframe,
        open_time=datetime(
            2026,
            8,
            16,
            tzinfo=UTC,
        )
        + timeframe.duration * index,
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


def zone(
    timeframe: Timeframe,
) -> IctZoneRecord:
    base = datetime(
        2026,
        8,
        16,
        tzinfo=UTC,
    )

    return IctZoneRecord(
        zone_id="zone-1",
        symbol="TESTUSDT",
        timeframe=timeframe,
        zone_type="OB",
        polarity="BULLISH",
        state="FRESH",
        grade="OB_A",
        band_low=Decimal("100"),
        band_high=Decimal("110"),
        refined_low=None,
        refined_high=None,
        created_index=0,
        confirmed_index=0,
        created_at=base,
        updated_at=base,
        parent_zone_id=None,
        dealing_range_id=None,
        stale_context=False,
        gap_adjacent=False,
        origin_swept=False,
        evidence="{}",
    )


@pytest.mark.asyncio
async def test_uniform_interactions_are_persisted() -> None:
    candles = pad_for_warmup(
        [
            make_candle(
                symbol="TESTUSDT",
                timeframe=Timeframe.M5,
                index=0,
                open_="115",
                high="116",
                low="114",
                close="115",
            ),
            make_candle(
                symbol="TESTUSDT",
                timeframe=Timeframe.M5,
                index=1,
                open_="112",
                high="113",
                low="104",
                close="111",
            ),
            make_candle(
                symbol="TESTUSDT",
                timeframe=Timeframe.M5,
                index=2,
                open_="105",
                high="108",
                low="95",
                close="99",
            ),
        ]
    )

    interactions = FakeInteractionRepository()

    service = IctZoneInteractionReplayService(
        FakeCandleRepository(candles),
        FakeContextRepository((zone(Timeframe.M5),)),
        interactions,
    )

    report = await service.run(
        "TESTUSDT",
        Timeframe.M5,
        candles[0].open_time,
        candles[-1].close_time,
    )

    kinds = {record.kind for record in interactions.records.values()}

    assert "TOUCH" in kinds
    assert "REJECTION" in kinds
    assert "MITIGATION" in kinds
    assert "RESPECT" in kinds
    assert "VIOLATION" in kinds

    assert report.interactions_inserted == len(interactions.records)


@pytest.mark.asyncio
async def test_interaction_replay_is_idempotent() -> None:
    candles = pad_for_warmup(
        [
            make_candle(
                symbol="TESTUSDT",
                timeframe=Timeframe.M5,
                index=0,
                open_="115",
                high="116",
                low="114",
                close="115",
            ),
            make_candle(
                symbol="TESTUSDT",
                timeframe=Timeframe.M5,
                index=1,
                open_="112",
                high="113",
                low="104",
                close="111",
            ),
        ]
    )

    interactions = FakeInteractionRepository()

    service = IctZoneInteractionReplayService(
        FakeCandleRepository(candles),
        FakeContextRepository((zone(Timeframe.M5),)),
        interactions,
    )

    first = await service.run(
        "TESTUSDT",
        Timeframe.M5,
        candles[0].open_time,
        candles[-1].close_time,
    )

    second = await service.run(
        "TESTUSDT",
        Timeframe.M5,
        candles[0].open_time,
        candles[-1].close_time,
    )

    assert first.interactions_inserted > 0
    assert second.interactions_inserted == 0


@pytest.mark.asyncio
async def test_respect_can_receive_ltf_confirmation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    parent = [
        make_candle(
            symbol="TESTUSDT",
            timeframe=Timeframe.M15,
            index=0,
            open_="115",
            high="116",
            low="114",
            close="115",
        ),
        make_candle(
            symbol="TESTUSDT",
            timeframe=Timeframe.M15,
            index=1,
            open_="112",
            high="113",
            low="104",
            close="111",
        ),
        make_candle(
            symbol="TESTUSDT",
            timeframe=Timeframe.M15,
            index=2,
            open_="116",
            high="117",
            low="115",
            close="116",
        ),
        make_candle(
            symbol="TESTUSDT",
            timeframe=Timeframe.M15,
            index=3,
            open_="117",
            high="118",
            low="116",
            close="117",
        ),
    ]

    parent = pad_for_warmup(parent)

    lower: list[Candle] = []

    for index in range(12):
        close = Decimal("105")

        if index == 7:
            close = Decimal("112")

        lower.append(
            make_candle(
                symbol="TESTUSDT",
                timeframe=Timeframe.M5,
                index=index,
                open_=str(close),
                high=str(close + Decimal("2")),
                low=str(close - Decimal("2")),
                close=str(close),
            )
        )

    lower = pad_for_warmup(lower)

    internal_swing = SwingPoint(
        index=5,
        open_time=lower[5].open_time,
        price=Decimal("108"),
        kind=SwingKind.HIGH,
        strength=SwingStrength.INTERNAL,
    )

    monkeypatch.setattr(
        interaction_module,
        "detect_internal_swings",
        lambda _: (internal_swing,),
    )

    monkeypatch.setattr(
        interaction_module,
        "swing_window",
        lambda _: 1,
    )

    interactions = FakeInteractionRepository()

    service = IctZoneInteractionReplayService(
        FakeCandleRepository(
            [
                *parent,
                *lower,
            ]
        ),
        FakeContextRepository((zone(Timeframe.M15),)),
        interactions,
    )

    report = await service.run(
        "TESTUSDT",
        Timeframe.M15,
        parent[0].open_time,
        parent[-1].close_time,
    )

    kinds = [record.kind for record in interactions.records.values()]

    assert "RESPECT" in kinds
    assert "CONFIRMATION" in kinds
    assert report.confirmations >= 1


@pytest.mark.asyncio
async def test_empty_interaction_history_is_safe() -> None:
    service = IctZoneInteractionReplayService(
        FakeCandleRepository([]),
        FakeContextRepository(()),
        FakeInteractionRepository(),
    )

    report = await service.run(
        "TESTUSDT",
        Timeframe.M5,
        datetime(2026, 8, 16, tzinfo=UTC),
        datetime(
            2026,
            8,
            17,
            tzinfo=UTC,
        ),
    )

    assert report.interactions_inserted == 0
    assert report.zones_evaluated == 0
