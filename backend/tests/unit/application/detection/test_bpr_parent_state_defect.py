"""SLS §5.6's parent-liveness rule, enforced (fixed after a pinned repro).

§5.6 Validation: *"Both parents must **still** be OPEN/TOUCHED at
registration."* The word *still* is the rule — a BPR must not be built on an
inefficiency price has already consumed.

For months it could not fail: `IctReplayService.run` detected every FVG first
(all snapshots OPEN by construction), composed BPRs second, and ran the
lifecycle third, so `compose_bpr`'s guard only ever saw birth certificates.
This file pinned that as `xfail(strict=True)` until the fix was made under a
version bump (ICT_ALGO_VERSION s6-v2 → s6-v3, per Constitution §44.5):
`_persist_bprs` now replays each parent to the registration index with
`replay_fvg_to`, so the guard sees what price has done since.

The premise test stays: without it, the enforcement test could pass for the
wrong reason — a series that quietly stopped producing the FILLED parent would
look exactly like the fix.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from tests.golden.harness.memory import (
    FixedClock,
    InMemoryCandleRepository,
    InMemoryIctZoneRepository,
    InMemoryIctZoneStateStore,
    InMemoryIctZoneTransitionRepository,
)
from tests.support.builders import pad_for_warmup

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
    candles = pad_for_warmup(build_candles())
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
    assert bullish[0].created_index == 295
    assert bullish[0].state == "FILLED", (
        "the bullish parent must be consumed at candle 3 for this repro to mean anything"
    )


async def test_bpr_must_not_compose_from_an_already_filled_parent() -> None:
    report, zones = await run_engine()

    assert report.bprs_created == 0, (  # type: ignore[attr-defined]
        "a BPR was composed from a parent FVG that price had already filled"
    )

    assert not [zone for zone in zones.zones.values() if zone.zone_type == "BPR"]


async def test_a_touched_parent_still_composes() -> None:
    """The other half of §5.6's sentence: TOUCHED is *still valid*.

    Guards against the over-correction — a replay that refused everything but
    OPEN would silently ban the memory's own worked example (a parent whose
    band was entered but whose CE held), and zero BPRs forever reads exactly
    like a market with none.
    """

    candles = pad_for_warmup(build_touched_candles())
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

    # Premise first: exactly the two parents, and the bullish one really is
    # TOUCHED -- not a third OPEN gap standing in for it.
    assert report.fvgs_detected == 2  # type: ignore[attr-defined]

    bullish = [
        zone
        for zone in zones.zones.values()
        if zone.zone_type == "FVG" and zone.polarity == "BULLISH"
    ]

    assert len(bullish) == 1
    assert bullish[0].state == "TOUCHED"

    assert report.bprs_created == 1  # type: ignore[attr-defined]


# The worked shape from the defect note: bullish FVG [100, 106] (CE 103);
# candle 3 dips only to 104, so the parent goes OPEN -> TOUCHED and no
# further; the bearish FVG [105, 107] then overlaps [105, 106] = 1 against a
# smaller band of 2, passing the >= 0.5 test.
#
# The first version of this fixture had candle 5's low at 107, which gapped
# over candle 3's high and quietly created a THIRD, still-OPEN bullish FVG --
# and the BPR composed from that one. The test passed while exercising
# nothing: the mutation that bans TOUCHED parents survived it. Candle 5's low
# now sits on 106 (no gap), and the premise assertions below hold the fixture
# to exactly two FVGs so it cannot drift like that again.
TOUCHED_OHLC = [
    ("99", "100", "98", "99"),
    ("99", "110", "99", "109"),
    ("109", "111", "106", "110"),
    ("105", "106", "104", "105"),
    ("109", "110", "107", "109"),
    ("108", "109", "106", "108"),
    ("104", "105", "104", "104"),
]


def build_touched_candles() -> list[Candle]:
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
        for index, (open_, high, low, close) in enumerate(TOUCHED_OHLC)
    ]
