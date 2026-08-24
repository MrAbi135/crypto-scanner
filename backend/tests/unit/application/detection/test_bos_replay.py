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


def body_candle(index: int, open_: str, close: str, *, low: str | None = None) -> Candle:
    """A candle with a body, for the one rule that reads which way it closed."""
    o, c = Decimal(open_), Decimal(close)

    return Candle(
        symbol=SYMBOL,
        timeframe=Timeframe.H1,
        open_time=T0 + timedelta(hours=index),
        open=o,
        high=max(o, c) + Decimal("1"),
        low=Decimal(low) if low is not None else min(o, c) - Decimal("1"),
        close=c,
        volume=Decimal("100"),
        quote_volume=Decimal("10000"),
        taker_buy_volume=Decimal("50"),
        trade_count=10,
        source=CandleSource.BACKFILL,
    )


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

    inserted, _ = await build_service(events, candles)._replay_bos(
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
async def test_an_outside_bar_closing_bearish_does_not_break_upward() -> None:
    """§3.5 edge case (1), which the engine did not implement.

    "One candle closes beyond both an external high and low (extreme outside
    bar): resolve by close direction -- bullish close => bullish BOS only; the
    opposite penetration becomes a sweep candidate."

    `_replay_bos` took its direction and its candidate swing kind from the
    trend state alone and never read the close's own direction, so a candle
    could only ever break the way the trend was already pointing.

    Candle 21 engulfs the structure: its high of 151 clears the 130 and its
    low of 95 undercuts the 110, so both extremes are taken and only one of
    them can be the break. It closes at 132 -- above the level -- but it
    opened at 150, so the body is bearish. Under edge case (1) that is not a
    bullish break.

    The break is withheld rather than replaced by a bearish one. §3.5's main
    rule requires "the trend agreeing" and the trend here is BULLISH, so a
    bearish BOS cannot satisfy both clauses; the doctrine contradicts itself
    in this corner, and this is the reading both halves survive. The downward
    penetration is a sweep candidate, which is the Liquidity Engine's to
    record and is exactly where edge case (1) sends it.
    """
    candles = [candle(i, "125") for i in range(21)] + [body_candle(21, "150", "132", low="95")]
    events = FakeEventRepository()

    inserted, _ = await build_service(events, candles)._replay_bos(
        symbol=SYMBOL,
        timeframe=Timeframe.H1,
        candles=candles,
        external_swings=tuple(UPTREND),
    )

    assert inserted == 0
    assert not [e for e in events.events.values() if e.event_type.startswith("BOS_")]


@pytest.mark.asyncio
async def test_an_outside_bar_closing_bullish_still_breaks_upward() -> None:
    """The other half, so the rule withholds a break rather than all of them.

    The same engulfing range -- high 136 over the 130, low 95 under the 110 --
    and the same close of 132. Only the body differs: this one opened at 125,
    so it closed bullish, and "bullish close => bullish BOS" is the rule's own
    affirmative case.
    """
    candles = [candle(i, "125") for i in range(21)] + [body_candle(21, "125", "132", low="95")]
    events = FakeEventRepository()

    inserted, _ = await build_service(events, candles)._replay_bos(
        symbol=SYMBOL,
        timeframe=Timeframe.H1,
        candles=candles,
        external_swings=tuple(UPTREND),
    )

    assert inserted == 1

    bos = [e for e in events.events.values() if e.event_type.startswith("BOS_")]

    assert len(bos) == 1
    assert bos[0].event_type == "BOS_UP"
    assert json.loads(bos[0].payload)["swing_price"] == "130"


@pytest.mark.asyncio
async def test_an_ordinary_bearish_candle_through_the_level_still_breaks() -> None:
    """The precondition has to bind, or edge case (1) swallows §3.5's main rule.

    This close is bearish too -- opened at 140, closed at 132 -- but its low of
    131 leaves the 110 untouched, so nothing about it is an outside bar. Only
    one extreme was taken, there is nothing to resolve between, and the break
    stands.

    Read as "the close is beyond both extremes" rather than "the range takes
    both", the rule would need a low priced above a high and would fire on
    almost nothing; read without any precondition it would gate every break in
    the engine on candle colour. This is the case that pins the middle.
    """
    candles = [candle(i, "125") for i in range(21)] + [body_candle(21, "140", "132", low="131")]
    events = FakeEventRepository()

    inserted, _ = await build_service(events, candles)._replay_bos(
        symbol=SYMBOL,
        timeframe=Timeframe.H1,
        candles=candles,
        external_swings=tuple(UPTREND),
    )

    assert inserted == 1


@pytest.mark.asyncio
async def test_a_level_price_already_left_behind_is_consumed_without_a_break() -> None:
    """The doctrinal question this test used to flag, now answered.

    It read: "the 120 level was passed by price around candle 0, roughly twenty
    candles before trend armed, and it is marked broken only now. §3.5 does not
    say whether a level price left behind while RANGING should still be
    breakable once trend arrives. This asserts what the engine does rather than
    what it ought to; flagged for review rather than quietly encoded as intent."

    It ought not to. §3.5 says "the break candle is the first closing candle
    beyond the level", and candle 22 is not that candle for the 120 -- price
    cleared it before the series began. Recording a break there dates a fact to
    a candle that did not make it, and because only the most recent unconsumed
    level is ever a candidate, it repeated: one manufactured break per candle,
    marching backwards through the level history.

    Measured on the VM before the change, 93 of 186 recorded breaks were of
    that shape -- half of every break the engine had ever reported. Settled by
    the developer 2026-08-23 on the strength of that count.

    So candle 21 breaks the 130 and consumes it, and the 120 goes with it as
    bookkeeping: under the close, therefore surpassed, therefore not a
    structural event. Candle 22 has nothing left to break.
    """

    candles = [candle(i, "125") for i in range(21)] + [
        candle(21, "135"),
        candle(22, "140"),
    ]
    events = FakeEventRepository()

    inserted, _ = await build_service(events, candles)._replay_bos(
        symbol=SYMBOL,
        timeframe=Timeframe.H1,
        candles=candles,
        external_swings=tuple(UPTREND),
    )

    assert inserted == 1

    broken = [e for e in events.events.values() if e.event_type.startswith("BOS_")]

    assert len(broken) == 1
    assert broken[0].event_at == candles[21].open_time

    # The 130 is the break. The 120 is consumed silently, so it can never be
    # broken later either -- which is the half of the rule a count of one
    # would not catch on its own.
    assert json.loads(broken[0].payload)["swing_price"] == "130"


@pytest.mark.asyncio
async def test_no_bos_while_structure_is_ranging() -> None:
    """§3.4 gates §3.5.

    Only two external highs and one low confirm here, so trend never leaves
    RANGING — and a close far above every high is still not a break.
    """

    ranging = UPTREND[:3]
    candles = [candle(i, "125") for i in range(11)] + [candle(11, "200")]
    events = FakeEventRepository()

    inserted, _ = await build_service(events, candles)._replay_bos(
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

    inserted, _ = await build_service(events, candles)._replay_bos(
        symbol=SYMBOL,
        timeframe=Timeframe.H1,
        candles=candles,
        external_swings=tuple(UPTREND),
    )

    assert inserted == 0
