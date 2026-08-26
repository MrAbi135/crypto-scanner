"""High-yield application coverage for Sprint S6 replay services."""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import ClassVar

import pytest
from tests.support.builders import pad_for_warmup

import scanner.application.detection.ict_ob_replay as ob_mod
import scanner.application.detection.ict_replay as ict_mod
from scanner.application.detection.ict_ob_replay import (
    IctOrderBlockReplayService,
)
from scanner.application.detection.ict_replay import (
    IctReplayService,
)
from scanner.application.ports.ict_evidence import (
    LiquidityEvidenceRecord,
    ShiftEvidenceRecord,
    StructureEvidenceRecord,
)
from scanner.application.ports.ict_zones import (
    IctZoneRecord,
    IctZoneTransitionRecord,
)
from scanner.domain.common import Candle, CandleSource
from scanner.domain.ict import (
    FvgState,
    IfvgState,
    ZonePolarity,
    ZoneState,
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
    TERMINAL: ClassVar[frozenset[str]] = frozenset(
        {
            "INVALIDATED",
            "EXPIRED",
            "FILLED",
            "INVERTED",
            "DEAD",
        }
    )

    def __init__(self) -> None:
        self.zones: dict[str, IctZoneRecord] = {}

    async def upsert(self, zone: IctZoneRecord) -> None:
        current = self.zones.get(zone.zone_id)

        if current is not None and current.state in self.TERMINAL:
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
                and zone.state not in self.TERMINAL
            )
        )

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

        self.zones[zone_id] = replace(
            zone,
            state=to_state,
            updated_at=updated_at,
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


class FakeSnapshotStore:
    def __init__(self) -> None:
        self.saved: tuple[IctZoneRecord, ...] = ()

    async def save(
        self,
        symbol: str,
        timeframe: Timeframe,
        zones: tuple[IctZoneRecord, ...],
    ) -> None:
        self.saved = zones

    async def delete(
        self,
        symbol: str,
        timeframe: Timeframe,
    ) -> None:
        self.saved = ()


class FakeEvidenceRepository:
    def __init__(
        self,
        structure: tuple[StructureEvidenceRecord, ...] = (),
        liquidity: tuple[LiquidityEvidenceRecord, ...] = (),
        shifts: tuple[ShiftEvidenceRecord, ...] = (),
    ) -> None:
        self.structure = structure
        self.liquidity = liquidity
        self.shifts = shifts

    async def list_structure(
        self,
        symbol: str,
        timeframe: Timeframe,
        start: datetime,
        end: datetime,
    ) -> tuple[StructureEvidenceRecord, ...]:
        return self.structure

    async def list_shifts(
        self,
        symbol: str,
        timeframe: Timeframe,
        start: datetime,
        end: datetime,
    ) -> tuple[ShiftEvidenceRecord, ...]:
        return self.shifts

    async def list_liquidity(
        self,
        symbol: str,
        timeframe: Timeframe,
        start: datetime,
        end: datetime,
    ) -> tuple[LiquidityEvidenceRecord, ...]:
        return self.liquidity


def fixture_series() -> list[Candle]:
    """Reproduce the proven S6SHIFT synthetic runtime shape."""

    candles: list[Candle] = []
    start = datetime(2026, 8, 16, 6, tzinfo=UTC)

    for n in range(55):
        if n <= 6:
            close = Decimal("100") + Decimal(n) * Decimal("1.3333333333")
        elif n <= 12:
            close = Decimal("108") - Decimal(n - 6) * Decimal("2.6666666667")
        elif n <= 18:
            close = Decimal("92") + Decimal(n - 12) * Decimal("4.3333333333")
        elif n <= 24:
            close = Decimal("118") - Decimal(n - 18) * Decimal("3.5")
        elif n <= 30:
            close = Decimal("97") + Decimal(n - 24) * Decimal("5.1666666667")
        elif n <= 36:
            close = Decimal("128") - Decimal(n - 30) * Decimal("4.3333333333")
        elif n == 37:
            close = Decimal("106")
        elif n == 38:
            close = Decimal("112")
        elif n == 39:
            close = Decimal("120")
        elif n == 40:
            close = Decimal("125")
        elif n == 41:
            close = Decimal("114")
        elif n == 42:
            close = Decimal("94")
        elif n == 43:
            close = Decimal("88")
        else:
            close = Decimal("88") + Decimal(n - 43) * Decimal("0.5")

        if n == 42:
            open_ = Decimal("114")
        elif n == 43:
            open_ = Decimal("93")
        elif 7 <= n <= 12 or 19 <= n <= 24 or 31 <= n <= 36:
            open_ = close + Decimal("1")
        else:
            open_ = close - Decimal("1")

        high = close + Decimal("2")
        low = close - Decimal("2")

        special_highs = {
            6: Decimal("110"),
            18: Decimal("120"),
            30: Decimal("130"),
            40: Decimal("132"),
            42: Decimal("116"),
            43: Decimal("95"),
        }

        special_lows = {
            12: Decimal("90"),
            24: Decimal("95"),
            36: Decimal("100"),
            37: Decimal("104"),
            38: Decimal("108"),
            39: Decimal("112"),
            40: Decimal("110"),
            41: Decimal("108"),
            42: Decimal("92"),
            43: Decimal("86"),
        }

        high = special_highs.get(n, high)
        low = special_lows.get(n, low)

        candles.append(
            Candle(
                symbol="S6COVUSDT",
                timeframe=Timeframe.M5,
                open_time=start + timedelta(minutes=n * 5),
                open=open_,
                high=high,
                low=low,
                close=close,
                volume=Decimal("5000") if n in {40, 42, 43} else Decimal("100"),
                quote_volume=(Decimal("500000") if n in {40, 42, 43} else Decimal("10000")),
                taker_buy_volume=(Decimal("1800") if n in {40, 42, 43} else Decimal("50")),
                trade_count=500 if n in {40, 42, 43} else 10,
                source=CandleSource.BACKFILL,
            )
        )

    return candles


def zone_record(
    *,
    zone_id: str,
    zone_type: str,
    state: str = "FRESH",
    polarity: str = "BULLISH",
    parent_zone_id: str | None = None,
    evidence: str = "{}",
    refined: bool = False,
    origin_swept: bool | None = False,
) -> IctZoneRecord:
    base = datetime(2026, 8, 16, 6, tzinfo=UTC)

    return IctZoneRecord(
        zone_id=zone_id,
        symbol="S6COVUSDT",
        timeframe=Timeframe.M5,
        zone_type=zone_type,
        polarity=polarity,
        state=state,
        grade="TEST",
        band_low=Decimal("100"),
        band_high=Decimal("110"),
        refined_low=Decimal("101") if refined else None,
        refined_high=Decimal("109") if refined else None,
        created_index=1,
        confirmed_index=3,
        created_at=base,
        updated_at=base,
        parent_zone_id=parent_zone_id,
        dealing_range_id=None,
        stale_context=False,
        gap_adjacent=False,
        origin_swept=origin_swept,
        evidence=evidence,
    )


@pytest.mark.asyncio
async def test_full_ict_replay_fixture_exercises_detection_and_lifecycle() -> None:
    candles = pad_for_warmup(fixture_series())
    zones = FakeZoneRepository()
    transitions = FakeTransitionRepository()
    snapshots = FakeSnapshotStore()

    service = IctReplayService(
        FakeCandleRepository(candles),
        zones,
        transitions,
        snapshots,
        FakeClock(),
    )

    report = await service.run(
        "S6COVUSDT",
        Timeframe.M5,
        candles[0].open_time,
        candles[-1].close_time,
    )

    assert report.candles == 300
    assert report.fvgs_detected > 0
    assert report.zones_upserted > 0
    assert snapshots.saved == await zones.list_live(
        "S6COVUSDT",
        Timeframe.M5,
    )


@pytest.mark.asyncio
async def test_full_ob_replay_fixture_exercises_detection() -> None:
    candles = pad_for_warmup(fixture_series())
    zones = FakeZoneRepository()
    snapshots = FakeSnapshotStore()

    structure = (
        StructureEvidenceRecord(
            event_type="SWING_EXTERNAL_HIGH",
            event_at=candles[30].open_time,
            algo_version="test",
            payload=json.dumps(
                {
                    "index": 30,
                    "price": "130",
                    "strength": "EXTERNAL",
                    "kind": "HIGH",
                }
            ),
        ),
        StructureEvidenceRecord(
            event_type="SWING_EXTERNAL_LOW",
            event_at=candles[36].open_time,
            algo_version="test",
            payload=json.dumps(
                {
                    "index": 36,
                    "price": "100",
                    "strength": "EXTERNAL",
                    "kind": "LOW",
                }
            ),
        ),
        StructureEvidenceRecord(
            event_type="SWING_INTERNAL_HIGH",
            event_at=candles[39].open_time,
            algo_version="test",
            payload=json.dumps(
                {
                    "index": 39,
                    "price": "120",
                    "strength": "INTERNAL",
                    "kind": "HIGH",
                }
            ),
        ),
    )

    liquidity = (
        LiquidityEvidenceRecord(
            pool_id="pool-1",
            from_state="ACTIVE",
            to_state="SWEPT",
            reason="liquidity_sweep",
            transitioned_at=candles[40].close_time,
            candle_index=40,
            evidence=json.dumps(
                {
                    "side": "BSL",
                    "reference_level": "130",
                }
            ),
        ),
    )

    service = IctOrderBlockReplayService(
        FakeCandleRepository(candles),
        zones,
        FakeTransitionRepository(),
        snapshots,
        FakeEvidenceRepository(structure, liquidity),
        FakeClock(),
    )

    report = await service.run(
        "S6COVUSDT",
        Timeframe.M5,
        candles[0].open_time,
        candles[-1].close_time,
    )

    assert report.candles == 300
    assert report.displacements >= 1
    assert snapshots.saved == await zones.list_live(
        "S6COVUSDT",
        Timeframe.M5,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("service_kind", ["ict", "ob"])
async def test_replay_empty_and_invalid_ranges(service_kind: str) -> None:
    zones = FakeZoneRepository()
    snapshots = FakeSnapshotStore()

    if service_kind == "ict":
        service = IctReplayService(
            FakeCandleRepository([]),
            zones,
            FakeTransitionRepository(),
            snapshots,
            FakeClock(),
        )
    else:
        service = IctOrderBlockReplayService(
            FakeCandleRepository([]),
            zones,
            FakeTransitionRepository(),
            snapshots,
            FakeEvidenceRepository(),
            FakeClock(),
        )

    start = datetime(2026, 8, 16, tzinfo=UTC)
    end = datetime(2026, 8, 17, tzinfo=UTC)

    report = await service.run(
        "S6COVUSDT",
        Timeframe.M5,
        start,
        end,
    )

    assert report.candles == 0

    with pytest.raises(
        ValueError,
        match="end must be greater than start",
    ):
        await service.run(
            "S6COVUSDT",
            Timeframe.M5,
            start,
            start,
        )


def test_ict_record_conversion_and_helpers() -> None:
    fvg = zone_record(
        zone_id="fvg-1",
        zone_type="FVG",
        state=next(iter(FvgState)).value,
    )

    assert ict_mod._record_to_fvg(fvg).fvg_id == "fvg-1"

    ifvg = zone_record(
        zone_id="ifvg-1",
        zone_type="IFVG",
        state=IfvgState.FRESH.value,
        parent_zone_id="fvg-1",
        evidence=json.dumps({"remaining_age": 5}),
    )

    converted_ifvg = ict_mod._record_to_ifvg(ifvg)
    assert converted_ifvg.parent_fvg_id == "fvg-1"

    bpr = zone_record(
        zone_id="bpr-1",
        zone_type="BPR",
        state="FRESH",
        evidence=json.dumps(
            {
                "parent_a_id": "a",
                "parent_b_id": "b",
            }
        ),
    )

    assert ict_mod._record_to_bpr(bpr).parent_a_id == "a"

    assert ict_mod._fvg_transition_reason(FvgState.TOUCHED) == "zone_touch"
    assert ict_mod._fvg_transition_reason(FvgState.INVERTED) == "close_through"
    assert ict_mod._ifvg_transition_reason(IfvgState.DEAD) == "flip_failed"

    # Determinism over identical inputs is what this used to assert, and it is
    # true of any hash — it could not fail. What matters is that the id is
    # built only from things that survive the window sliding, so the same
    # transition seen on two different passes is the same transition.
    transition_a = ict_mod._build_transition_id(
        zone_id="z",
        from_state="A",
        to_state="B",
        transitioned_at=datetime(2026, 8, 16, tzinfo=UTC),
    )

    transition_b = ict_mod._build_transition_id(
        zone_id="z",
        from_state="A",
        to_state="B",
        transitioned_at=datetime(2026, 8, 16, tzinfo=UTC),
    )

    assert transition_a == transition_b

    # And a different candle is a different transition.
    assert transition_a != ict_mod._build_transition_id(
        zone_id="z",
        from_state="A",
        to_state="B",
        transitioned_at=datetime(2026, 8, 16, 1, tzinfo=UTC),
    )


@pytest.mark.parametrize(
    ("record", "message"),
    [
        (
            zone_record(
                zone_id="bad",
                zone_type="OB",
            ),
            "record is not an FVG",
        ),
        (
            zone_record(
                zone_id="bad",
                zone_type="IFVG",
                evidence="{}",
                parent_zone_id="p",
            ),
            "IFVG evidence missing remaining_age",
        ),
        (
            zone_record(
                zone_id="bad",
                zone_type="IFVG",
                evidence=json.dumps({"remaining_age": 3}),
            ),
            "IFVG missing parent zone",
        ),
        (
            zone_record(
                zone_id="bad",
                zone_type="BPR",
                evidence="{}",
            ),
            "BPR evidence missing parent_a_id",
        ),
    ],
)
def test_ict_record_validation(record: IctZoneRecord, message: str) -> None:
    if record.zone_type == "IFVG":
        target = ict_mod._record_to_ifvg
    elif record.zone_type == "BPR":
        target = ict_mod._record_to_bpr
    else:
        target = ict_mod._record_to_fvg

    with pytest.raises(ValueError, match=message):
        target(record)


def test_ob_evidence_parsers_and_structure_helpers() -> None:
    now = datetime(2026, 8, 16, tzinfo=UTC)

    records = (
        StructureEvidenceRecord(
            event_type="STRUCTURE_EXTERNAL_HH",
            event_at=now,
            algo_version="test",
            payload="{}",
        ),
        StructureEvidenceRecord(
            event_type="SWING_EXTERNAL_HIGH",
            event_at=now,
            algo_version="test",
            payload=json.dumps(
                {
                    "index": 5,
                    "price": "110",
                    "strength": "EXTERNAL",
                    "kind": "HIGH",
                }
            ),
        ),
        StructureEvidenceRecord(
            event_type="SWING_INTERNAL_LOW",
            event_at=now,
            algo_version="test",
            payload=json.dumps(
                {
                    "index": 6,
                    "price": "95",
                    "strength": "INTERNAL",
                    "kind": "LOW",
                }
            ),
        ),
    )

    swings = ob_mod._parse_swings(records)

    assert len(swings) == 2

    liquidity = (
        LiquidityEvidenceRecord(
            pool_id="ignored",
            from_state="ACTIVE",
            to_state="BROKEN",
            reason="liquidity_break",
            transitioned_at=now,
            candle_index=4,
            evidence="{}",
        ),
        LiquidityEvidenceRecord(
            pool_id="swept",
            from_state="ACTIVE",
            to_state="SWEPT",
            reason="liquidity_sweep",
            transitioned_at=now,
            candle_index=7,
            evidence=json.dumps(
                {
                    "side": "BSL",
                    "reference_level": "110",
                }
            ),
        ),
    )

    sweeps = ob_mod._parse_liquidity_sweeps(liquidity)

    assert len(sweeps) == 1

    assert ob_mod._latest_break_level(
        swings,
        strength="EXTERNAL",
        direction=ob_mod.DisplacementDirection.BULLISH,
        before_index=10,
    ) == Decimal("110")

    assert ob_mod._closes_beyond_level(
        Decimal("111"),
        ob_mod.DisplacementDirection.BULLISH,
        Decimal("110"),
    )

    assert not ob_mod._closes_beyond_level(
        Decimal("100"),
        ob_mod.DisplacementDirection.BULLISH,
        None,
    )


def test_ob_record_conversion_and_validation() -> None:
    ob = zone_record(
        zone_id="ob-1",
        zone_type="OB",
        state=ZoneState.FRESH.value,
        refined=True,
        evidence=json.dumps(
            {
                "origin_failure_swing": True,
            }
        ),
    )

    converted = ob_mod._record_to_ob(ob)

    assert converted.ob_id == "ob-1"
    assert converted.origin_failure_swing is True

    breaker = zone_record(
        zone_id="breaker-1",
        zone_type="BREAKER",
        state=ZoneState.FRESH.value,
        parent_zone_id="ob-1",
        refined=True,
        origin_swept=True,
        evidence=json.dumps({"gap_break": True}),
    )

    assert ob_mod._record_to_breaker(breaker).gap_break is True

    mitigation = zone_record(
        zone_id="mit-1",
        zone_type="MITIGATION",
        state=ZoneState.FRESH.value,
        parent_zone_id="ob-1",
        refined=True,
    )

    assert ob_mod._record_to_mitigation(mitigation).parent_ob_id == "ob-1"

    assert ob_mod._ob_transition_reason(ZoneState.TESTED) == "zone_test"
    assert ob_mod._breaker_transition_reason(ZoneState.INVALIDATED) == "breaker_failed"
    assert ob_mod._mitigation_transition_reason(ZoneState.MITIGATED) == "mitigation_mitigation"


def test_failure_swing_and_origin_sweep_helpers() -> None:
    ob_record = zone_record(
        zone_id="ob-2",
        zone_type="OB",
        state=ZoneState.INVALIDATED.value,
        refined=True,
        polarity=ZonePolarity.BULLISH.value,
        evidence=json.dumps(
            {
                "origin_failure_swing": False,
            }
        ),
    )

    ob = ob_mod._record_to_ob(ob_record)

    sweep = ob_mod.LiquiditySweepEvidence(
        pool_id="ssl",
        side="SSL",
        reference_level=Decimal("105"),
        candle_index=2,
        transitioned_at=datetime(2026, 8, 16, tzinfo=UTC),
    )

    assert ob_mod._origin_has_sweep(
        ob,
        (sweep,),
    )

    swings = (
        ob_mod.SwingEvidence(
            index=4,
            price=Decimal("102"),
            strength="EXTERNAL",
            kind="LOW",
        ),
        ob_mod.SwingEvidence(
            index=5,
            price=Decimal("104"),
            strength="EXTERNAL",
            kind="LOW",
        ),
    )

    assert ob_mod._has_failure_swing_before_invalidation(
        ob,
        swings,
        invalidation_index=6,
    )


@pytest.mark.asyncio
async def test_transition_helpers_cover_success_and_failure() -> None:
    zones = FakeZoneRepository()
    transitions = FakeTransitionRepository()
    snapshots = FakeSnapshotStore()
    candles = pad_for_warmup(fixture_series())

    record = zone_record(
        zone_id="fvg-transition",
        zone_type="FVG",
        state=next(iter(FvgState)).value,
    )

    await zones.upsert(record)

    ict_service = IctReplayService(
        FakeCandleRepository(candles),
        zones,
        transitions,
        snapshots,
        FakeClock(),
    )

    changed = await ict_service._transition_zone(
        record=record,
        from_state=next(iter(FvgState)).value,
        to_state=FvgState.TOUCHED.value,
        reason="zone_touch",
        candle_index=4,
        transitioned_at=candles[4].close_time,
        evidence={"test": True},
    )

    assert changed
    assert transitions.transitions

    unchanged = await ict_service._transition_zone(
        record=record,
        from_state="DOES_NOT_MATCH",
        to_state=FvgState.TOUCHED.value,
        reason="noop",
        candle_index=4,
        transitioned_at=candles[4].close_time,
        evidence={},
    )

    assert not unchanged


async def _ob_grades(shifts: tuple[ShiftEvidenceRecord, ...]) -> set[str]:
    """Run the OB replay over the shared fixture and collect the zone grades."""
    candles = pad_for_warmup(fixture_series())
    zones = FakeZoneRepository()

    service = IctOrderBlockReplayService(
        FakeCandleRepository(candles),
        zones,
        FakeTransitionRepository(),
        FakeSnapshotStore(),
        FakeEvidenceRepository((), (), shifts),
        FakeClock(),
    )

    await service.run(
        "S6COVUSDT",
        Timeframe.M5,
        candles[0].open_time,
        candles[-1].close_time,
    )

    return {zone.grade for zone in zones.zones.values() if zone.zone_type == "OB"}


def _mss(direction: str, choch: int, followthrough: int) -> ShiftEvidenceRecord:
    return ShiftEvidenceRecord(
        event_type=f"MSS_{direction}",
        direction=direction,
        choch_index=choch,
        followthrough_index=followthrough,
        event_at=datetime(2026, 8, 16, 6, tzinfo=UTC),
        payload=json.dumps({"direction": direction}),
    )


@pytest.mark.asyncio
async def test_an_mss_over_the_displacement_grades_the_ob_a() -> None:
    """§5.1: OB_A "if the qualifying move broke external structure **or the
    candidate sits at the origin of an MSS**".

    `mss_origin` was a literal `False`, so the second half of the grade rule
    never fired and OB_A could only ever come from an external break. The
    fixture supplies no structure evidence at all here, so an OB_A can only
    have come from the MSS.
    """
    without = await _ob_grades(())
    covering = await _ob_grades((_mss("UP", 0, 400), _mss("DOWN", 0, 400)))

    assert without, "fixture produced no order blocks, so this proves nothing"
    assert without == {"OB_B"}
    assert "OB_A" in covering


@pytest.mark.asyncio
async def test_an_mss_elsewhere_in_the_window_does_not_grade_the_ob_a() -> None:
    """The span has to contain the displacement, not merely exist.

    §3.6 builds an MSS from a CHoCH plus displacement plus follow-through, so
    its span is where its move happened -- an MSS two hundred candles away is
    not the origin of this block.
    """
    elsewhere = await _ob_grades((_mss("UP", 0, 5), _mss("DOWN", 0, 5)))

    assert elsewhere == {"OB_B"}


@pytest.mark.asyncio
async def test_the_mss_direction_vocabulary_is_translated() -> None:
    """§3.6 says UP/DOWN; §5.10 says BULLISH/BEARISH.

    Both are `str`, so comparing them directly type-checks and yields False
    forever -- the trap that silently produced zero stop hunts before PR #20.
    A record spelled the displacement's way must not match.
    """
    mistyped = await _ob_grades(
        (_mss("BULLISH", 0, 400), _mss("BEARISH", 0, 400)),
    )

    assert mistyped == {"OB_B"}
