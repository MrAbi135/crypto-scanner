"""Tests for chronological CHoCH/MSS replay."""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from tests.golden.harness.memory import InMemoryEngineStateStore

import scanner.application.detection.structure_shift_replay as shift_module
from scanner.application.detection.state import (
    SHIFT_NAMESPACE,
    EngineStateManager,
)
from scanner.application.detection.structure_shift_replay import (
    StructureShiftReplayService,
)
from scanner.application.ports.detection import EngineEventRecord
from scanner.application.ports.ict_evidence import (
    LiquidityEvidenceRecord,
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
from scanner.shared.errors import DomainInvariantError


class FakeClock:
    def now(self) -> datetime:
        return datetime(
            2026,
            8,
            16,
            12,
            tzinfo=UTC,
        )


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


class FakeEventRepository:
    def __init__(self) -> None:
        self.events: dict[
            str,
            EngineEventRecord,
        ] = {}

    async def append(
        self,
        event: EngineEventRecord,
    ) -> bool:
        if event.event_key in self.events:
            return False

        self.events[event.event_key] = event
        return True

    async def exists(
        self,
        event_key: str,
    ) -> bool:
        return event_key in self.events


class FakeEvidenceRepository:
    def __init__(
        self,
        liquidity: tuple[
            LiquidityEvidenceRecord,
            ...,
        ],
    ) -> None:
        self.liquidity = liquidity

    async def list_structure(
        self,
        symbol: str,
        timeframe: Timeframe,
        start: datetime,
        end: datetime,
    ) -> tuple:
        return ()

    async def list_liquidity(
        self,
        symbol: str,
        timeframe: Timeframe,
        start: datetime,
        end: datetime,
    ) -> tuple[
        LiquidityEvidenceRecord,
        ...,
    ]:
        return self.liquidity


def candles() -> list[Candle]:
    result: list[Candle] = []

    for index in range(30):
        open_price = Decimal("100")
        close_price = Decimal("101")
        high = Decimal("102")
        low = Decimal("99")

        if index == 20:
            open_price = Decimal("105")
            close_price = Decimal("90")
            high = Decimal("106")
            low = Decimal("89")

        elif index == 21:
            open_price = Decimal("91")
            close_price = Decimal("87")
            high = Decimal("92")
            low = Decimal("86")

        result.append(
            Candle(
                symbol="BTCUSDT",
                timeframe=Timeframe.H1,
                open_time=datetime(
                    2026,
                    8,
                    1,
                    tzinfo=UTC,
                )
                + timedelta(hours=index),
                open=open_price,
                high=high,
                low=low,
                close=close_price,
                volume=Decimal("100"),
                quote_volume=Decimal("10000"),
                taker_buy_volume=Decimal("50"),
                trade_count=10,
                source=CandleSource.BACKFILL,
            )
        )

    return result


def external_swings(
    series: list[Candle],
) -> tuple[SwingPoint, ...]:
    return (
        SwingPoint(
            1,
            series[1].open_time,
            Decimal("100"),
            SwingKind.HIGH,
            SwingStrength.EXTERNAL,
        ),
        SwingPoint(
            2,
            series[2].open_time,
            Decimal("80"),
            SwingKind.LOW,
            SwingStrength.EXTERNAL,
        ),
        SwingPoint(
            4,
            series[4].open_time,
            Decimal("110"),
            SwingKind.HIGH,
            SwingStrength.EXTERNAL,
        ),
        SwingPoint(
            5,
            series[5].open_time,
            Decimal("85"),
            SwingKind.LOW,
            SwingStrength.EXTERNAL,
        ),
        SwingPoint(
            7,
            series[7].open_time,
            Decimal("120"),
            SwingKind.HIGH,
            SwingStrength.EXTERNAL,
        ),
        SwingPoint(
            8,
            series[8].open_time,
            Decimal("95"),
            SwingKind.LOW,
            SwingStrength.EXTERNAL,
        ),
    )


@pytest.mark.asyncio
async def test_external_sweep_choch_confirms_mss(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    series = candles()

    monkeypatch.setattr(
        shift_module,
        "detect_external_swings",
        lambda _: external_swings(series),
    )

    monkeypatch.setattr(
        shift_module,
        "detect_internal_swings",
        lambda _: (),
    )

    sweep_evidence = json.dumps(
        {
            "liquidity_class": "EXTERNAL",
            "side": "BSL",
        }
    )

    evidence = FakeEvidenceRepository(
        (
            LiquidityEvidenceRecord(
                pool_id="external-bsl",
                from_state="ACTIVE",
                to_state="SWEPT",
                reason="liquidity_sweep",
                transitioned_at=series[18].close_time,
                candle_index=18,
                evidence=sweep_evidence,
            ),
        )
    )

    events = FakeEventRepository()

    service = StructureShiftReplayService(
        FakeCandleRepository(series),
        events,
        evidence,
        FakeClock(),
        EngineStateManager(InMemoryEngineStateStore(), namespace=SHIFT_NAMESPACE),
    )

    report = await service.run(
        "BTCUSDT",
        Timeframe.H1,
        series[0].open_time,
        series[-1].close_time,
    )

    event_types = {event.event_type for event in events.events.values()}

    assert "CHOCH_DOWN" in event_types
    assert "MSS_DOWN" in event_types

    assert report.choch_created == 1
    assert report.mss_created == 1
    assert report.trend_state == "BEARISH"


@pytest.mark.asyncio
async def test_structure_shift_replay_is_idempotent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    series = candles()

    monkeypatch.setattr(
        shift_module,
        "detect_external_swings",
        lambda _: external_swings(series),
    )

    monkeypatch.setattr(
        shift_module,
        "detect_internal_swings",
        lambda _: (),
    )

    evidence = FakeEvidenceRepository(
        (
            LiquidityEvidenceRecord(
                pool_id="external-bsl",
                from_state="ACTIVE",
                to_state="SWEPT",
                reason="liquidity_sweep",
                transitioned_at=series[18].close_time,
                candle_index=18,
                evidence=json.dumps(
                    {
                        "liquidity_class": "EXTERNAL",
                        "side": "BSL",
                    }
                ),
            ),
        )
    )

    events = FakeEventRepository()

    service = StructureShiftReplayService(
        FakeCandleRepository(series),
        events,
        evidence,
        FakeClock(),
        EngineStateManager(InMemoryEngineStateStore(), namespace=SHIFT_NAMESPACE),
    )

    first = await service.run(
        "BTCUSDT",
        Timeframe.H1,
        series[0].open_time,
        series[-1].close_time,
    )

    second = await service.run(
        "BTCUSDT",
        Timeframe.H1,
        series[0].open_time,
        series[-1].close_time,
    )

    assert first.events_inserted == 2
    assert second.events_inserted == 0


@pytest.mark.asyncio
async def test_empty_shift_history_is_safe() -> None:
    service = StructureShiftReplayService(
        FakeCandleRepository([]),
        FakeEventRepository(),
        FakeEvidenceRepository(()),
        FakeClock(),
        EngineStateManager(InMemoryEngineStateStore(), namespace=SHIFT_NAMESPACE),
    )

    report = await service.run(
        "BTCUSDT",
        Timeframe.H1,
        datetime(
            2026,
            8,
            1,
            tzinfo=UTC,
        ),
        datetime(
            2026,
            8,
            2,
            tzinfo=UTC,
        ),
    )

    assert report.choch_created == 0
    assert report.mss_created == 0
    assert report.events_inserted == 0
    assert report.trend_state == "RANGING"


@pytest.mark.asyncio
async def test_unparseable_liquidity_evidence_raises_rather_than_downgrading(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A corrupt evidence blob must be loud, not quietly answer "no sweep".

    This is the same fixture as ``test_external_sweep_choch_confirms_mss`` --
    which proves the series does confirm an MSS when the evidence parses -- with
    the blob replaced by garbage. Before the fix the JSONDecodeError was
    swallowed with ``continue``, so the sweep check simply returned False and
    the run downgraded MSS_DOWN to a bare CHOCH_DOWN. Nothing failed; the
    doctrine answer was just wrong, permanently and without a trace.

    Asserting on the raise (rather than on the downgraded output) is deliberate:
    it is the swallow that Constitution §8.5 prohibits, not the downgrade.
    """
    series = candles()

    monkeypatch.setattr(
        shift_module,
        "detect_external_swings",
        lambda _: external_swings(series),
    )

    monkeypatch.setattr(
        shift_module,
        "detect_internal_swings",
        lambda _: (),
    )

    evidence = FakeEvidenceRepository(
        (
            LiquidityEvidenceRecord(
                pool_id="external-bsl",
                from_state="ACTIVE",
                to_state="SWEPT",
                reason="liquidity_sweep",
                transitioned_at=series[18].close_time,
                candle_index=18,
                evidence="{not json at all",
            ),
        )
    )

    service = StructureShiftReplayService(
        FakeCandleRepository(series),
        FakeEventRepository(),
        evidence,
        FakeClock(),
        EngineStateManager(InMemoryEngineStateStore(), namespace=SHIFT_NAMESPACE),
    )

    with pytest.raises(DomainInvariantError) as caught:
        await service.run(
            "BTCUSDT",
            Timeframe.H1,
            series[0].open_time,
            series[-1].close_time,
        )

    # The pool must be named, or the operator cannot find the bad row.
    assert caught.value.details["pool_id"] == "external-bsl"
    assert caught.value.details["candle_index"] == 18


def _service(series, evidence_rows):
    events = FakeEventRepository()

    service = StructureShiftReplayService(
        FakeCandleRepository(series),
        events,
        FakeEvidenceRepository(evidence_rows),
        FakeClock(),
        EngineStateManager(InMemoryEngineStateStore(), namespace=SHIFT_NAMESPACE),
    )

    return service, events


def _sweep_row(series, *, reference_level: str | None):
    payload: dict = {"liquidity_class": "EXTERNAL", "side": "BSL"}

    if reference_level is not None:
        payload["reference_level"] = reference_level

    return LiquidityEvidenceRecord(
        pool_id="external-bsl",
        from_state="ACTIVE",
        to_state="SWEPT",
        reason="liquidity_sweep",
        transitioned_at=series[18].close_time,
        candle_index=18,
        evidence=json.dumps(payload),
    )


@pytest.mark.asyncio
async def test_a_new_confirmed_hh_supersedes_the_choch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """§3.4's recovery edge and §3.8's "Superseded by new HH/LL".

    The CHoCH prints at 20; a higher high pivoted at 17 confirms at 22
    (k_ext = 5). Doctrine restores BULLISH and withdraws the warning -- so
    the follow-through that would have confirmed a bearish MSS afterwards
    must find no candidate left to confirm.
    """
    series = candles()
    # Defuse the original fixture's immediate follow-through at 21...
    series[21] = replace(series[21], open=Decimal("91"), close=Decimal("91"), low=Decimal("90"))
    # ...and give 23 a close that WOULD be follow-through if the candidate
    # were still alive.
    series[23] = replace(series[23], open=Decimal("95"), close=Decimal("88"), low=Decimal("87"))

    swings = (
        *external_swings(series),
        SwingPoint(
            17,
            series[17].open_time,
            Decimal("130"),
            SwingKind.HIGH,
            SwingStrength.EXTERNAL,
        ),
    )

    monkeypatch.setattr(shift_module, "detect_external_swings", lambda _: swings)
    monkeypatch.setattr(shift_module, "detect_internal_swings", lambda _: ())

    service, events = _service(series, (_sweep_row(series, reference_level=None),))

    report = await service.run("BTCUSDT", Timeframe.H1, series[0].open_time, series[-1].close_time)

    event_types = {event.event_type for event in events.events.values()}

    assert "CHOCH_DOWN" in event_types
    assert "MSS_DOWN" not in event_types, "a superseded warning must not confirm"
    assert report.trend_state == "BULLISH"


@pytest.mark.asyncio
async def test_a_reclaim_within_ten_candles_demotes_the_mss_to_ranging(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """§3.6's invalidation: close back beyond the pre-MSS extreme within 10
    candles -> trend demoted to RANGING, MSS marked low_quality by an
    APPENDED fact carrying the original event's key (the table is
    append-only; a fact about a prior event is a follow-up event)."""

    series = candles()
    # Candle 24 reclaims the swept high (120).
    series[24] = replace(series[24], open=Decimal("118"), close=Decimal("121"), high=Decimal("122"))

    monkeypatch.setattr(shift_module, "detect_external_swings", lambda _: external_swings(series))
    monkeypatch.setattr(shift_module, "detect_internal_swings", lambda _: ())

    service, events = _service(series, (_sweep_row(series, reference_level="120"),))

    report = await service.run("BTCUSDT", Timeframe.H1, series[0].open_time, series[-1].close_time)

    invalidations = [
        e for e in events.events.values() if e.event_type == "STRUCTURE_MSS_INVALIDATED_DOWN"
    ]
    mss_events = [e for e in events.events.values() if e.event_type == "MSS_DOWN"]

    assert report.trend_state == "RANGING"
    assert len(invalidations) == 1

    payload = json.loads(invalidations[0].payload)

    assert payload["low_quality"] is True
    assert payload["mss_event_key"] == mss_events[0].event_key
    assert payload["pre_mss_extreme"] == "120"


@pytest.mark.asyncio
async def test_a_reclaim_after_the_window_changes_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    series = candles()
    # Stretch the series and reclaim only at candle 33 -- 12 candles after
    # the MSS at 21, outside §3.6's 10.
    while len(series) < 36:
        last = series[-1]
        series.append(replace(last, open_time=last.open_time + timedelta(hours=1)))

    series[33] = replace(series[33], open=Decimal("118"), close=Decimal("121"), high=Decimal("122"))

    monkeypatch.setattr(shift_module, "detect_external_swings", lambda _: external_swings(series))
    monkeypatch.setattr(shift_module, "detect_internal_swings", lambda _: ())

    service, events = _service(series, (_sweep_row(series, reference_level="120"),))

    report = await service.run("BTCUSDT", Timeframe.H1, series[0].open_time, series[-1].close_time)

    assert report.trend_state == "BEARISH"
    assert not [e for e in events.events.values() if "MSS_INVALIDATED" in e.event_type]


@pytest.mark.asyncio
async def test_the_sweep_origin_is_found_by_time_not_by_its_frozen_index(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The sweep row's candle_index froze in whichever window recorded it
    (live sweeps freeze near ~497). Judged by index against today's CHoCH at
    20, this sweep does not exist and the MSS silently downgrades to a
    warning; judged by transitioned_at it is two candles before the CHoCH,
    exactly §3.6's origin 2(a)."""

    series = candles()

    monkeypatch.setattr(shift_module, "detect_external_swings", lambda _: external_swings(series))
    monkeypatch.setattr(shift_module, "detect_internal_swings", lambda _: ())

    frozen_junk = LiquidityEvidenceRecord(
        pool_id="external-bsl",
        from_state="ACTIVE",
        to_state="SWEPT",
        reason="liquidity_sweep",
        transitioned_at=series[18].close_time,
        candle_index=497,
        evidence=json.dumps({"liquidity_class": "EXTERNAL", "side": "BSL"}),
    )

    service, _ = _service(series, (frozen_junk,))

    report = await service.run("BTCUSDT", Timeframe.H1, series[0].open_time, series[-1].close_time)

    assert report.mss_created == 1
    assert report.trend_state == "BEARISH"
