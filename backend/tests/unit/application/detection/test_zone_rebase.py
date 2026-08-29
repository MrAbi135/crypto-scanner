"""Frozen zone indices rebased into today's window (the 2026-08-29 review).

The upsert never revises created/confirmed_index (identity must not drift),
so they freeze at the recording window's offsets -- tail-clustered, because
live detection happens at the tail. Ages computed against them could not
grow, so tail-frozen zones could never expire (host: 21 of 909 FVGs, 0 of
372 BPRs), and resuming from the frozen confirmation skipped restart gaps.
"""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal

from tests.golden.harness.memory import (
    FixedClock,
    InMemoryCandleRepository,
    InMemoryIctZoneRepository,
    InMemoryIctZoneStateStore,
    InMemoryIctZoneTransitionRepository,
)
from tests.support.builders import make_candle

from scanner.application.detection.ict_replay import ICT_ALGO_VERSION, IctReplayService
from scanner.application.detection.window_time import rebased_indices
from scanner.application.ports.ict_zones import IctZoneRecord
from scanner.shared import Timeframe

T0 = datetime(2026, 1, 1, tzinfo=UTC)
TF = Timeframe.H1


def flat_candles(count: int, *, start: datetime) -> list:
    # Flat and gapless: no new FVGs detect, so the lifecycle under test is
    # the only thing moving.
    return [
        make_candle(
            open_time=start + TF.duration * offset,
            open_=Decimal("100"),
            high=Decimal("101"),
            low=Decimal("99"),
            close=Decimal("100"),
        )
        for offset in range(count)
    ]


def fvg_record(*, created_at: datetime, frozen_index: int) -> IctZoneRecord:
    return IctZoneRecord(
        zone_id="fvg-old",
        symbol="BTCUSDT",
        timeframe=TF,
        zone_type="FVG",
        polarity="BULLISH",
        state="OPEN",
        grade="FVG_B",
        band_low=Decimal("90"),
        band_high=Decimal("92"),
        refined_low=None,
        refined_high=None,
        created_index=frozen_index,
        confirmed_index=frozen_index,
        created_at=created_at,
        updated_at=created_at,
        parent_zone_id=None,
        dealing_range_id=None,
        stale_context=False,
        gap_adjacent=False,
        origin_swept=False,
        evidence=json.dumps({"algo_version": ICT_ALGO_VERSION}),
    )


def test_an_in_window_zone_rebases_to_its_true_position() -> None:
    candles = flat_candles(100, start=T0)

    # Created at candle 40's close; frozen at 497 by a long-gone window.
    record = fvg_record(created_at=T0 + TF.duration * 41, frozen_index=497)

    created, confirmed = rebased_indices(record, candles, TF)

    assert created == 40
    assert confirmed == 40


def test_a_pre_window_zone_rebases_negative_which_is_the_truth() -> None:
    candles = flat_candles(100, start=T0)

    record = fvg_record(created_at=T0 - TF.duration * 49, frozen_index=497)

    created, _ = rebased_indices(record, candles, TF)

    # 50 candles before the window opened: age at candle i is i + 50.
    assert created == -50


def test_the_confirmation_rides_as_a_same_window_delta() -> None:
    candles = flat_candles(100, start=T0)

    record = fvg_record(created_at=T0 + TF.duration * 11, frozen_index=490)
    record = replace(record, confirmed_index=493)

    created, confirmed = rebased_indices(record, candles, TF)

    assert created == 10
    assert confirmed == 13


async def test_a_tail_frozen_fvg_finally_expires() -> None:
    """The behaviour the frozen index made impossible.

    This FVG is 250 candles old and OPEN. Its frozen index (497) told the
    old arithmetic it was ~2 candles young forever; rebased, its true age
    crosses the 200-candle cap and §5.4's expiry fires.
    """
    window = flat_candles(500, start=T0)
    zones = InMemoryIctZoneRepository()

    old_zone = fvg_record(
        # 250 candles before the window's newest candle.
        created_at=window[-1].open_time - TF.duration * 249,
        frozen_index=497,
    )

    await zones.upsert(old_zone)

    service = IctReplayService(
        InMemoryCandleRepository(window),
        zones,
        InMemoryIctZoneTransitionRepository(),
        InMemoryIctZoneStateStore(),
        FixedClock(T0),
    )

    await service.run(
        "BTCUSDT",
        TF,
        window[0].open_time,
        window[-1].open_time + TF.duration,
    )

    assert zones.zones["fvg-old"].state == "EXPIRED"


async def test_a_zone_confirmed_before_the_window_still_replays_the_window() -> None:
    """The resume clamp: a pre-window confirmation starts the walk at candle
    0 instead of skipping the whole window -- restart gaps are finally seen.

    The band's ONLY touches live in the window's first ten candles; price
    then leaves for good. A resume from the frozen tail offset walks only
    candles the zone never meets and leaves it OPEN -- flat candles that
    touch on every bar could not tell the two resumes apart, which is how
    this test's first draft let that mutation survive."""

    head = flat_candles(10, start=T0)
    away = [
        make_candle(
            open_time=T0 + TF.duration * (10 + offset),
            open_=Decimal("200"),
            high=Decimal("201"),
            low=Decimal("199"),
            close=Decimal("200"),
        )
        for offset in range(490)
    ]
    window = head + away
    zones = InMemoryIctZoneRepository()

    old_zone = fvg_record(
        created_at=T0 - TF.duration * 10,
        frozen_index=497,
    )
    # Inside the head's range only; the away leg never comes back.
    old_zone = replace(old_zone, band_low=Decimal("99.5"), band_high=Decimal("100.5"))

    await zones.upsert(old_zone)

    service = IctReplayService(
        InMemoryCandleRepository(window),
        zones,
        InMemoryIctZoneTransitionRepository(),
        InMemoryIctZoneStateStore(),
        FixedClock(T0),
    )

    await service.run(
        "BTCUSDT",
        TF,
        window[0].open_time,
        window[-1].open_time + TF.duration,
    )

    # FILLED specifically: the head candles wick through the distal edge.
    # A tail-only resume cannot produce FILLED -- it can still produce
    # EXPIRED (age fires anywhere in the walk), which is how an earlier
    # "anything but OPEN" assertion let the frozen-resume mutation pass.
    assert zones.zones["fvg-old"].state == "FILLED"


async def test_a_tail_frozen_untested_ob_finally_expires() -> None:
    """§5.1: EXPIRED at age > 250 without a test -- and OB age is measured
    from CONFIRMATION, so rebasing created_index alone would leave a
    mixed-frame object whose age still cannot grow. Far band: no candle ever
    tests it, so the only exit is the expiry this used to be unable to reach.
    """
    from tests.unit.application.detection.test_ict_replay_coverage import (
        FakeCandleRepository,
        FakeClock,
        FakeEvidenceRepository,
        FakeSnapshotStore,
        FakeTransitionRepository,
        FakeZoneRepository,
    )

    from scanner.application.detection.ict_ob_replay import (
        ICT_OB_ALGO_VERSION,
        IctOrderBlockReplayService,
    )

    window = flat_candles(500, start=T0)

    record = fvg_record(
        created_at=window[-1].open_time - TF.duration * 299,
        frozen_index=497,
    )
    record = replace(
        record,
        zone_id="ob-old",
        zone_type="OB",
        state="FRESH",
        grade="OB_B",
        band_low=Decimal("10"),
        band_high=Decimal("12"),
        refined_low=Decimal("10.5"),
        refined_high=Decimal("11.5"),
        evidence=json.dumps({"algo_version": ICT_OB_ALGO_VERSION}),
    )

    zones = FakeZoneRepository()
    await zones.upsert(record)

    service = IctOrderBlockReplayService(
        FakeCandleRepository(window),
        zones,
        FakeTransitionRepository(),
        FakeSnapshotStore(),
        FakeEvidenceRepository((), ()),
        FakeClock(),
    )

    await service.run(
        "BTCUSDT",
        TF,
        window[0].open_time,
        window[-1].open_time + TF.duration,
    )

    assert zones.zones["ob-old"].state == "EXPIRED"
