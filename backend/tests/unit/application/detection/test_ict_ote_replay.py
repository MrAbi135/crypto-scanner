"""Tests for OTE/PD replay service."""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from scanner.application.detection.ict_ote_replay import (
    IctOteReplayService,
)
from scanner.application.ports.ict_zones import (
    IctZoneRecord,
    IctZoneTransitionRecord,
)
from scanner.domain.common import (
    Candle,
    CandleSource,
)
from scanner.shared import Timeframe


class FakeClock:
    def now(self) -> datetime:
        return datetime(2026, 8, 16, 12, tzinfo=UTC)


class FakeCandleRepository:
    def __init__(self, candles: list[Candle]) -> None:
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


class FakeZoneRepository:
    def __init__(self) -> None:
        self.zones: dict[str, IctZoneRecord] = {}

    async def upsert(self, zone: IctZoneRecord) -> None:
        current = self.zones.get(zone.zone_id)

        if current is not None and current.state in {
            "INVALIDATED",
            "EXPIRED",
        }:
            return

        self.zones[zone.zone_id] = zone

    async def get(self, zone_id: str) -> IctZoneRecord | None:
        return self.zones.get(zone_id)

    async def list_live(
        self,
        symbol: str,
        timeframe: Timeframe,
    ) -> tuple[IctZoneRecord, ...]:
        return tuple(
            zone
            for zone in self.zones.values()
            if (
                zone.symbol == symbol
                and zone.timeframe is timeframe
                and zone.state
                not in {
                    "INVALIDATED",
                    "EXPIRED",
                }
            )
        )

    async def list_open(
        self,
        symbol: str,
        timeframe: Timeframe,
    ) -> tuple[IctZoneRecord, ...]:
        return await self.list_live(symbol, timeframe)

    async def transition(
        self,
        zone_id: str,
        *,
        from_state: str,
        to_state: str,
        updated_at: datetime,
    ) -> bool:
        zone = self.zones.get(zone_id)

        if zone is None or zone.state != from_state:
            return False

        self.zones[zone_id] = IctZoneRecord(
            zone_id=zone.zone_id,
            symbol=zone.symbol,
            timeframe=zone.timeframe,
            zone_type=zone.zone_type,
            polarity=zone.polarity,
            state=to_state,
            grade=zone.grade,
            band_low=zone.band_low,
            band_high=zone.band_high,
            refined_low=zone.refined_low,
            refined_high=zone.refined_high,
            created_index=zone.created_index,
            confirmed_index=zone.confirmed_index,
            created_at=zone.created_at,
            updated_at=updated_at,
            parent_zone_id=zone.parent_zone_id,
            dealing_range_id=zone.dealing_range_id,
            stale_context=zone.stale_context,
            gap_adjacent=zone.gap_adjacent,
            origin_swept=zone.origin_swept,
            evidence=zone.evidence,
        )

        return True


class FakeTransitionRepository:
    def __init__(self) -> None:
        self.transitions: dict[str, IctZoneTransitionRecord] = {}

    async def append(
        self,
        transition: IctZoneTransitionRecord,
    ) -> bool:
        if transition.transition_id in self.transitions:
            return False

        self.transitions[transition.transition_id] = transition
        return True


def series() -> list[Candle]:
    candles: list[Candle] = []

    prices = [
        100,
        102,
        104,
        106,
        108,
        110,
        107,
        104,
        101,
        98,
        95,
        92,
        96,
        101,
        106,
        112,
        118,
        124,
        120,
        115,
        110,
        105,
        100,
        97,
        102,
        108,
        115,
        122,
        128,
        134,
        130,
        125,
        120,
        115,
        110,
        105,
        100,
        104,
        108,
        112,
        116,
        120,
        116,
        112,
        108,
        104,
        100,
        96,
        92,
        88,
    ]

    for index, price in enumerate(prices):
        close = Decimal(str(price))

        candles.append(
            Candle(
                symbol="OTEUSDT",
                timeframe=Timeframe.M5,
                open_time=datetime(
                    2026,
                    8,
                    16,
                    6,
                    tzinfo=UTC,
                )
                + timedelta(minutes=5 * index),
                open=close - Decimal("1"),
                high=close + Decimal("2"),
                low=close - Decimal("2"),
                close=close,
                volume=Decimal("100"),
                quote_volume=Decimal("10000"),
                taker_buy_volume=Decimal("50"),
                trade_count=10,
                source=CandleSource.BACKFILL,
            )
        )

    return candles


@pytest.mark.asyncio
async def test_ote_replay_persists_zones() -> None:
    candles = series()
    zones = FakeZoneRepository()

    service = IctOteReplayService(
        FakeCandleRepository(candles),
        zones,
        FakeTransitionRepository(),
        FakeClock(),
    )

    report = await service.run(
        "OTEUSDT",
        Timeframe.M5,
        candles[0].open_time,
        candles[-1].close_time,
    )

    ote_zones = [zone for zone in zones.zones.values() if zone.zone_type == "OTE"]

    assert report.impulse_legs > 0
    assert report.otes_detected > 0
    assert report.zones_upserted > 0
    assert ote_zones


@pytest.mark.asyncio
async def test_ote_replay_is_idempotent() -> None:
    candles = series()
    zones = FakeZoneRepository()
    transitions = FakeTransitionRepository()

    service = IctOteReplayService(
        FakeCandleRepository(candles),
        zones,
        transitions,
        FakeClock(),
    )

    await service.run(
        "OTEUSDT",
        Timeframe.M5,
        candles[0].open_time,
        candles[-1].close_time,
    )

    first_count = len(zones.zones)
    first_transition_count = len(transitions.transitions)

    await service.run(
        "OTEUSDT",
        Timeframe.M5,
        candles[0].open_time,
        candles[-1].close_time,
    )

    assert len(zones.zones) == first_count
    assert len(transitions.transitions) == first_transition_count


@pytest.mark.asyncio
async def test_an_ote_on_an_already_decided_candle_is_not_created_again() -> None:
    """Audit M3 (owner ruling 2026-09-14): `detect_ote` sizes the band against
    ATR seeded at the window start, so a later pass could create an OTE about an
    old candle. A pass creates OTEs only after the last candle it decided."""
    from tests.golden.harness.memory import InMemoryEngineStateStore

    from scanner.application.detection.ict_ote_replay import ICT_OTE_ALGO_VERSION
    from scanner.application.detection.state import (
        ICT_OTE_NAMESPACE,
        EngineStateManager,
        StructureEngineState,
    )

    candles = series()
    zones = FakeZoneRepository()
    state = EngineStateManager(InMemoryEngineStateStore(), namespace=ICT_OTE_NAMESPACE)
    await state.save(
        StructureEngineState(
            symbol="OTEUSDT",
            timeframe=Timeframe.M5.value,
            algo_version=ICT_OTE_ALGO_VERSION,
            last_processed_open_time=candles[-1].open_time.isoformat(),
        )
    )

    report = await IctOteReplayService(
        FakeCandleRepository(candles),
        zones,
        FakeTransitionRepository(),
        FakeClock(),
        state=state,
    ).run("OTEUSDT", Timeframe.M5, candles[0].open_time, candles[-1].close_time)

    assert report.otes_detected == 0
    assert not [zone for zone in zones.zones.values() if zone.zone_type == "OTE"]


@pytest.mark.asyncio
async def test_a_leg_is_validated_once_on_the_candle_it_finalizes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """SLS v1.0.11 (owner ruling 2026-09-19). §5.8's `leg >= 2 x ATR` was asked
    on every candle the leg stayed the newest, against that candle's ATR, so a
    leg too short when it finalized registered once ATR had fallen -- dated to
    its leg-end, every interaction since written at once. Here ATR is large up
    to the finalization candle and small after it: the leg fails where it
    finalizes and gets no OTE."""
    import scanner.application.detection.ict_ote_replay as ote_module
    from scanner.domain.structure import detect_external_swings

    # `series()` up to the candle the 98 -> 122 leg finalizes on (index 46),
    # then four closes that stay inside its dealing range -- in `series()`
    # price leaves the range at once, so no later candle ever asked about the
    # leg and the old per-candle validation could not be seen.
    base = series()[:47]
    step = base[1].open_time - base[0].open_time
    candles = [
        *base,
        *(
            replace(
                base[-1],
                open_time=base[-1].open_time + step * (offset + 1),
                open=Decimal(close) - 1,
                high=Decimal(close) + 1,
                low=Decimal(close) - 1,
                close=Decimal(close),
            )
            for offset, close in enumerate(("101", "102", "101", "102"))
        ),
    ]
    swings = detect_external_swings(candles)
    found = ote_module._impulse_leg_at(swings, candles, len(candles) - 1)
    assert found is not None
    leg, finalized = found

    # Stated, not taken from the code under test: the leg ends on the 122
    # high at index 41, and §3.1's k = 5 finalizes it five candles later.
    assert (leg.end_index, finalized) == (41, 46)

    # The premise: after it finalizes the leg stays the newest one, inside its
    # dealing range, on every candle to the end.
    assert finalized < len(candles) - 1
    for index in range(finalized, len(candles)):
        again = ote_module._impulse_leg_at(swings, candles, index)
        assert again is not None and again[0].leg_id == leg.leg_id
        assert ote_module._dealing_range_at(swings, candles, index) is not None

    small = leg.length / Decimal(4)  # 2 x ATR well inside the leg
    large = leg.length  # 2 x ATR longer than the leg

    async def otes_for_leg(atrs) -> list[str]:
        monkeypatch.setattr(ote_module, "wilder_atr_series", lambda series_: atrs)
        zones = FakeZoneRepository()
        await IctOteReplayService(
            FakeCandleRepository(candles),
            zones,
            FakeTransitionRepository(),
            FakeClock(),
        ).run("OTEUSDT", Timeframe.M5, candles[0].open_time, candles[-1].close_time)
        return [
            zone.zone_id
            for zone in zones.zones.values()
            if zone.zone_type == "OTE" and json.loads(zone.evidence)["leg_id"] == leg.leg_id
        ]

    # The premise: at a steady small ATR the leg registers its OTE.
    assert await otes_for_leg([small] * len(candles))

    drifting = [large if index <= finalized else small for index in range(len(candles))]
    assert await otes_for_leg(drifting) == []


@pytest.mark.asyncio
async def test_empty_ote_history_is_safe() -> None:
    service = IctOteReplayService(
        FakeCandleRepository([]),
        FakeZoneRepository(),
        FakeTransitionRepository(),
        FakeClock(),
    )

    report = await service.run(
        "OTEUSDT",
        Timeframe.M5,
        datetime(2026, 8, 16, tzinfo=UTC),
        datetime(2026, 8, 17, tzinfo=UTC),
    )

    assert report.otes_detected == 0
    assert report.live_otes == 0
