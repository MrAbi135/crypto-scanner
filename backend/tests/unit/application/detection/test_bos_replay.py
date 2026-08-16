"""BOS orchestration through the structure service (SLS §3.5).

`detect_bos` itself is unit-tested. What had no coverage at all is
`StructureReplayService._replay_bos` — the orchestration around it, which is
where the doctrine actually lives:

* BOS is **armed by trend**, and trend comes from external structure only
  (§3.4). A close through a swing high is not a BOS in a RANGING market.
* Trend is recomputed **at each candle** from the swings confirmed *by that
  candle*, never from hindsight. §3.1's confirmation delay means a swing does
  not exist for the engine until `k` candles after its pivot.
* A broken swing is **consumed** — the same level cannot break twice.

Swings are constructed directly rather than grown from candles. Detecting them
is `detect_swings`' job and is covered elsewhere and by the golden suite;
manufacturing a forty-candle series that happens to yield six external swings
would test that instead of this, at ten times the size.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from tests.unit.application.detection.test_structure_replay import (
    FakeCandleRepository,
    FakeClock,
    FakeEventRepository,
    FakeStateStore,
)

from scanner.application.detection.state import EngineStateManager
from scanner.application.detection.structure_replay import StructureReplayService
from scanner.domain.common import Candle, CandleSource
from scanner.domain.structure import SwingKind, SwingPoint, SwingStrength
from scanner.shared import Timeframe

T0 = datetime(2026, 8, 1, tzinfo=UTC)
SYMBOL = "BOSUSDT"


def candle(index: int, close: str) -> Candle:
    value = Decimal(close)
    return Candle(
        symbol=SYMBOL,
        timeframe=Timeframe.H1,
        open_time=T0 + timedelta(hours=index),
        open=value,
        high=value + Decimal("1"),
        low=value - Decimal("1"),
        close=value,
        volume=Decimal("100"),
        quote_volume=Decimal("10000"),
        taker_buy_volume=Decimal("50"),
        trade_count=10,
        source=CandleSource.BACKFILL,
    )


def swing(index: int, price: str, kind: SwingKind) -> SwingPoint:
    return SwingPoint(
        index=index,
        open_time=T0 + timedelta(hours=index),
        price=Decimal(price),
        kind=kind,
        strength=SwingStrength.EXTERNAL,
    )


# Rising external structure: highs 110 -> 120 -> 130 label SEED, HH, HH and
# lows 100 -> 105 -> 110 label SEED, HL, HL, which is what §3.4 requires
# before it will leave RANGING.
UPTREND = [
    swing(0, "110", SwingKind.HIGH),
    swing(3, "100", SwingKind.LOW),
    swing(6, "120", SwingKind.HIGH),
    swing(9, "105", SwingKind.LOW),
    swing(12, "130", SwingKind.HIGH),
    swing(15, "110", SwingKind.LOW),
]


def build_service(events: FakeEventRepository, candles: list[Candle]):
    return StructureReplayService(
        FakeCandleRepository(candles),
        events,
        EngineStateManager(FakeStateStore()),
        FakeClock(),
    )


@pytest.mark.asyncio
async def test_a_close_through_a_swing_high_is_a_bos_once_trend_is_bullish() -> None:
    # The last swing confirms at candle 20 (index 15 + k_ext 5). Candle 21
    # closes above the 130 high; candle 20 deliberately does not.
    candles = [candle(i, "125") for i in range(21)] + [candle(21, "135")]
    events = FakeEventRepository()

    inserted = await build_service(events, candles)._replay_bos(
        symbol=SYMBOL,
        timeframe=Timeframe.H1,
        candles=candles,
        external_swings=tuple(UPTREND),
    )

    assert inserted == 1

    bos = [e for e in events.events.values() if e.event_type.startswith("BOS_")]

    assert len(bos) == 1
    assert bos[0].event_type == "BOS_UP"
    assert bos[0].event_at == candles[21].open_time


@pytest.mark.asyncio
async def test_a_broken_swing_is_consumed_and_the_next_level_is_taken() -> None:
    """§3.5 consumption, plus a behaviour worth a doctrinal look.

    Candle 21 breaks the 130 high and consumes it. Candle 22 closes at 140 and
    the engine then breaks the *next* unconsumed high, the 120 from index 6 —
    producing a second BOS.

    Consumption itself is right: 130 is not broken twice. But the 120 level was
    passed by price around candle 0, roughly twenty candles before trend armed,
    and it is marked broken only now. §3.5 does not say whether a level price
    left behind while RANGING should still be breakable once trend arrives.
    This asserts what the engine does rather than what it ought to; flagged for
    review rather than quietly encoded as intent.
    """

    candles = [candle(i, "125") for i in range(21)] + [
        candle(21, "135"),
        candle(22, "140"),
    ]
    events = FakeEventRepository()

    inserted = await build_service(events, candles)._replay_bos(
        symbol=SYMBOL,
        timeframe=Timeframe.H1,
        candles=candles,
        external_swings=tuple(UPTREND),
    )

    assert inserted == 2

    broken = sorted(
        (e for e in events.events.values() if e.event_type.startswith("BOS_")),
        key=lambda e: e.event_at,
    )

    assert [e.event_at for e in broken] == [
        candles[21].open_time,
        candles[22].open_time,
    ]

    # The 130 high is taken first, then the older 120 — never 130 twice.
    prices = [json.loads(e.payload)["swing_price"] for e in broken]

    assert prices == ["130", "120"]


@pytest.mark.asyncio
async def test_no_bos_while_structure_is_ranging() -> None:
    """§3.4 gates §3.5.

    Only two external highs and one low confirm here, so trend never leaves
    RANGING — and a close far above every high is still not a break.
    """

    ranging = UPTREND[:3]
    candles = [candle(i, "125") for i in range(11)] + [candle(11, "200")]
    events = FakeEventRepository()

    inserted = await build_service(events, candles)._replay_bos(
        symbol=SYMBOL,
        timeframe=Timeframe.H1,
        candles=candles,
        external_swings=tuple(ranging),
    )

    assert inserted == 0
    assert not events.events


@pytest.mark.asyncio
async def test_a_swing_cannot_break_before_it_has_confirmed() -> None:
    """§3.1's confirmation delay, enforced at the orchestration level.

    The break candle sits at index 19 — after the 130 high's pivot but before
    the final low confirms at 20 — so trend is still RANGING and no BOS fires.
    Hindsight is exactly what a non-repainting engine must not use.
    """

    candles = [candle(i, "125") for i in range(19)] + [candle(19, "135")]
    events = FakeEventRepository()

    inserted = await build_service(events, candles)._replay_bos(
        symbol=SYMBOL,
        timeframe=Timeframe.H1,
        candles=candles,
        external_swings=tuple(UPTREND),
    )

    assert inserted == 0
