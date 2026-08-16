"""Repro for the SLS §5.6 parent-liveness rule not being enforced.

§5.6 Validation: *"Both parents must **still** be OPEN/TOUCHED at
registration."* The word *still* is the rule — a BPR must not be built on an
inefficiency that price has already consumed.

`IctReplayService.run` cannot enforce it as written. It runs in three passes:

1. detect every FVG, collecting freshly-built objects into ``detected_fvgs``;
2. ``_persist_bprs(fvgs=detected_fvgs, ...)`` — BPR composition;
3. ``_replay_fvg_lifecycle(...)`` — only now do FVGs advance.

`compose_bpr` does check ``state not in {OPEN, TOUCHED}``, but every object it
receives carries the dataclass default ``OPEN``, so the guard can never fail.

The series below makes that concrete: a bullish FVG is FILLED at candle 3, and
a bearish FVG four candles later still pairs with it.

Fixing this changes detector output, which Constitution §44.5 classifies as a
logic change requiring versioning and a spec revision — not a drive-by edit.
So the defect is pinned here as ``xfail(strict=True)`` rather than patched: it
stays visible, `main` stays green, and the day someone fixes it this test goes
XPASS and fails, prompting its own removal.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from tests.golden.harness.memory import (
    FixedClock,
    InMemoryCandleRepository,
    InMemoryIctZoneRepository,
    InMemoryIctZoneStateStore,
    InMemoryIctZoneTransitionRepository,
)

from scanner.application.detection.ict_replay import IctReplayService
from scanner.domain.common import Candle, CandleSource
from scanner.shared import Timeframe

SYMBOL = "BPRDEFECT"
TF = Timeframe.H1
T0 = datetime(2026, 1, 5, tzinfo=UTC)

# Bullish FVG [100, 106] confirms at index 2; candle 3 wicks to 99 through its
# distal edge but closes at 105 inside it, which §5.4 makes FILLED. A bearish
# FVG [102, 108] then confirms at index 6, overlapping [102, 106] — four wide
# against a smaller band of six, clearing §5.6's 50% test.
OHLC = [
    ("99", "100", "98", "99"),
    ("99", "110", "99", "109"),
    ("109", "111", "106", "110"),
    ("105", "106", "99", "105"),
    ("109", "110", "108", "109"),
    ("105", "106", "100", "101"),
    ("101", "102", "98", "99"),
]


def build_candles() -> list[Candle]:
    return [
        Candle(
            symbol=SYMBOL,
            timeframe=TF,
            open_time=T0 + timedelta(hours=index),
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
        for index, (open_, high, low, close) in enumerate(OHLC)
    ]


async def run_engine() -> tuple[object, InMemoryIctZoneRepository]:
    candles = build_candles()
    zones = InMemoryIctZoneRepository()

    service = IctReplayService(
        InMemoryCandleRepository(candles),
        zones,
        InMemoryIctZoneTransitionRepository(),
        InMemoryIctZoneStateStore(),
        FixedClock(T0),
    )

    report = await service.run(
        SYMBOL,
        TF,
        candles[0].open_time,
        candles[-1].open_time + TF.duration,
    )

    return report, zones


async def test_premise_the_bullish_parent_is_consumed_before_the_pair_forms() -> None:
    """Guards the repro itself: the setup must really consume the parent.

    Without this, the xfail below could start passing for the wrong reason —
    a series that quietly stopped producing the FILLED parent would look like
    a fix.
    """

    report, zones = await run_engine()

    assert report.fvgs_detected == 2  # type: ignore[attr-defined]

    bullish = [
        zone
        for zone in zones.zones.values()
        if zone.zone_type == "FVG" and zone.polarity == "BULLISH"
    ]

    assert len(bullish) == 1
    assert bullish[0].created_index == 2
    assert bullish[0].state == "FILLED", (
        "the bullish parent must be consumed at candle 3 for this repro to mean anything"
    )


@pytest.mark.xfail(
    strict=True,
    reason=(
        "SLS §5.6 requires both parents to still be OPEN/TOUCHED at registration. "
        "BPR composition runs before the FVG lifecycle, so compose_bpr only ever "
        "sees detection-time OPEN snapshots and the guard cannot fail. Fixing it "
        "changes detector output (Constitution §44.5) and needs a versioned "
        "decision. When fixed, this test XPASSes and should be deleted."
    ),
)
async def test_bpr_must_not_compose_from_an_already_filled_parent() -> None:
    report, zones = await run_engine()

    assert report.bprs_created == 0, (  # type: ignore[attr-defined]
        "a BPR was composed from a parent FVG that price had already filled"
    )

    assert not [zone for zone in zones.zones.values() if zone.zone_type == "BPR"]
