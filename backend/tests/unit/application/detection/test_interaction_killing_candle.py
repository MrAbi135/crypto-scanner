"""The interaction pass decides each candle once, and still reaches a zone's killing candle.

The lifecycles run before the interaction pass, so a zone retired on the newest
candle was already terminal when the pass read its zones -- and production read
only live ones. That candle's interactions were never written: on the host, no
FVG, IFVG, BPR, OB or mitigation-block VIOLATION since the 09-13 deploy (OB 0 of
2,055 interactions while 293 order blocks were invalidated). The golden harness
listed every zone and recorded them, certifying behaviour production lacked.

Re-walking the whole window each pass is not the answer either: old candles are
re-decided against a window-start ATR, and the harness wrote 426 interactions
more than 100 candles late on a DOGEUSDT M15 replay. So a pass reads live zones
plus the zones retired on candles it has not decided, and walks a zone it walked
before only over those candles (audit M3).

These run against the golden harness's context repository, which mirrors the
production query.
"""

from __future__ import annotations

import json
from datetime import datetime
from decimal import Decimal

import pytest
from tests.golden.harness.memory import (
    InMemoryCandleRepository,
    InMemoryEngineStateStore,
    InMemoryIctZoneInteractionContextRepository,
    InMemoryIctZoneInteractionRepository,
    InMemoryIctZoneRepository,
    InMemoryIctZoneTransitionRepository,
)
from tests.support.builders import BASE_TIME, make_candle, pad_for_warmup

from scanner.application.detection.ict_interaction_replay import (
    ICT_INTERACTION_ALGO_VERSION,
    IctZoneInteractionReplayService,
)
from scanner.application.detection.state import (
    ICT_INTERACTION_NAMESPACE,
    EngineStateManager,
    StructureEngineState,
)
from scanner.application.ports.ict_zones import IctZoneRecord, IctZoneTransitionRecord
from scanner.domain.common import Candle
from scanner.shared import Timeframe

SYMBOL = "BTCUSDT"
TF = Timeframe.H1


def series(*, violate: bool) -> list[Candle]:
    """Above a bullish [100, 110] zone, a touch at scenario candle 1, then (when
    `violate`) a close through it at candle 2, then three quiet candles above."""
    shapes = [
        ("115", "116", "114", "115"),
        ("112", "113", "104", "111"),
        ("105", "108", "95", "99") if violate else ("115", "116", "114", "115"),
        ("130", "131", "129", "130"),
        ("130", "131", "129", "130"),
        ("130", "131", "129", "130"),
    ]
    start = BASE_TIME + TF.duration * 400

    return pad_for_warmup(
        [
            make_candle(
                symbol=SYMBOL,
                timeframe=TF,
                open_time=start + TF.duration * index,
                open_=Decimal(open_),
                high=Decimal(high),
                low=Decimal(low),
                close=Decimal(close),
            )
            for index, (open_, high, low, close) in enumerate(shapes)
        ]
    )


def at(candles: list[Candle], scenario_index: int) -> Candle:
    return candles[len(candles) - 6 + scenario_index]


def ob(candles: list[Candle], zone_id: str, state: str = "FRESH") -> IctZoneRecord:
    created = at(candles, 0).open_time + TF.duration  # created at candle 0's close

    return IctZoneRecord(
        zone_id=zone_id,
        symbol=SYMBOL,
        timeframe=TF,
        zone_type="OB",
        polarity="BULLISH",
        state=state,
        grade="OB_A",
        band_low=Decimal("100"),
        band_high=Decimal("110"),
        refined_low=None,
        refined_high=None,
        created_index=499,
        confirmed_index=499,
        created_at=created,
        updated_at=created,
        parent_zone_id=None,
        dealing_range_id=None,
        stale_context=False,
        gap_adjacent=False,
        origin_swept=False,
        evidence="{}",
    )


def invalidated(zone: IctZoneRecord, candle: Candle) -> IctZoneTransitionRecord:
    return IctZoneTransitionRecord(
        transition_id=f"t-{zone.zone_id}",
        zone_id=zone.zone_id,
        symbol=SYMBOL,
        timeframe=TF,
        zone_type=zone.zone_type,
        from_state="FRESH",
        to_state="INVALIDATED",
        reason="close_through",
        transitioned_at=candle.open_time + TF.duration,
        candle_index=0,
        evidence="{}",
    )


class RecordingContext(InMemoryIctZoneInteractionContextRepository):
    asked: datetime | None = None

    async def list_zones(self, symbol, timeframe, *, terminal_since=None):
        self.asked = terminal_since
        return await super().list_zones(symbol, timeframe, terminal_since=terminal_since)


async def decided_through(candle: Candle, walked: list[str]) -> EngineStateManager:
    state = EngineStateManager(InMemoryEngineStateStore(), namespace=ICT_INTERACTION_NAMESPACE)
    await state.save(
        StructureEngineState(
            symbol=SYMBOL,
            timeframe=TF.value,
            algo_version=ICT_INTERACTION_ALGO_VERSION,
            last_processed_open_time=candle.open_time.isoformat(),
            detail=json.dumps(walked),
        )
    )
    return state


async def replay(
    candles: list[Candle],
    zones: list[IctZoneRecord],
    transitions: list[IctZoneTransitionRecord],
    state: EngineStateManager | None,
):
    zone_store = InMemoryIctZoneRepository()
    for zone in zones:
        await zone_store.upsert(zone)

    transition_store = InMemoryIctZoneTransitionRepository()
    for transition in transitions:
        await transition_store.append(transition)

    context = RecordingContext(zone_store, transition_store)
    interactions = InMemoryIctZoneInteractionRepository()

    report = await IctZoneInteractionReplayService(
        InMemoryCandleRepository(candles), context, interactions, state=state
    ).run(SYMBOL, TF, candles[0].open_time, candles[-1].open_time + TF.duration)

    return report, {item.kind for item in interactions.interactions}, context


@pytest.mark.asyncio
async def test_a_zone_killed_on_an_undecided_candle_gets_its_violation() -> None:
    candles = series(violate=True)
    zone = ob(candles, "z-killed", state="INVALIDATED")
    state = await decided_through(at(candles, 1), walked=[zone.zone_id])

    _, kinds, _ = await replay(candles, [zone], [invalidated(zone, at(candles, 2))], state)

    assert "VIOLATION" in kinds


@pytest.mark.asyncio
async def test_a_zone_retired_on_a_decided_candle_is_not_read_again() -> None:
    candles = series(violate=True)
    zone = ob(candles, "z-dead", state="INVALIDATED")
    state = await decided_through(candles[-1], walked=[zone.zone_id])

    report, kinds, _ = await replay(candles, [zone], [invalidated(zone, at(candles, 2))], state)

    assert report.zones_evaluated == 0
    assert kinds == set()


@pytest.mark.asyncio
async def test_a_zone_walked_before_is_not_walked_over_decided_candles() -> None:
    candles = series(violate=False)
    zone = ob(candles, "z-live")

    _, walked_before, _ = await replay(
        candles, [zone], [], await decided_through(candles[-1], walked=[zone.zone_id])
    )
    # The premise: the same zone, new to the engine, is walked from its
    # confirmation and records the touch at candle 1.
    _, new_zone, _ = await replay(
        candles, [zone], [], await decided_through(candles[-1], walked=[])
    )

    assert walked_before == set()
    assert "TOUCH" in new_zone


@pytest.mark.asyncio
async def test_the_zone_read_reaches_back_over_the_confirmation_rewalk() -> None:
    candles = series(violate=False)

    _, _, decided = await replay(
        candles, [ob(candles, "z")], [], await decided_through(at(candles, 3), walked=[])
    )
    _, _, fresh = await replay(candles, [ob(candles, "z")], [], None)

    # Decided through candle 3: candle 4 is the first undecided, and the two
    # before it are walked again for a respect's lower-timeframe confirmation.
    assert decided.asked == at(candles, 2).open_time
    # With no record, the whole window.
    assert fresh.asked == candles[0].open_time


@pytest.mark.asyncio
async def test_a_pass_records_its_newest_candle_and_the_live_zones_it_walked() -> None:
    candles = series(violate=True)
    live = ob(candles, "z-live")
    killed = ob(candles, "z-killed", state="INVALIDATED")
    state = EngineStateManager(InMemoryEngineStateStore(), namespace=ICT_INTERACTION_NAMESPACE)

    await replay(candles, [live, killed], [invalidated(killed, at(candles, 2))], state)

    saved = await state.load(SYMBOL, TF.value, ICT_INTERACTION_ALGO_VERSION)

    assert saved is not None
    assert saved.last_processed_open_time == candles[-1].open_time.isoformat()
    assert json.loads(saved.detail or "[]") == ["z-live"]
