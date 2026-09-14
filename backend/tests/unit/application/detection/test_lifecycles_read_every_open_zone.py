"""Every open zone's lifecycle runs, not only the newest 60 (§5.1, audit M9).

`list_live` returns the newest `MAX_ZONES` zones: the set §8 scores. The
lifecycles read it too, so a zone outside that set was never advanced or
expired, and back-wrote its transitions hundreds of candles late when newer
zones died and it re-entered the top 60 (BTCUSDT H1 replay: an FVG at position
68 of 71; LINKUSDT M5: INVERTED written 466 candles late). domain/ict/state.py
already says such a zone "still transitions".

Each test hides the zone from `list_live` -- exactly what the bound does to an
old zone -- and offers it through `list_open`, then asserts its lifecycle ran.
"""

from __future__ import annotations

import pytest
from tests.support.builders import pad_for_warmup
from tests.unit.application.detection.test_ict_replay_coverage import (
    FakeCandleRepository,
    FakeClock,
    FakeEvidenceRepository,
    FakeSnapshotStore,
    FakeTransitionRepository,
    FakeZoneRepository,
    fixture_series,
    zone_record,
)

from scanner.application.detection.ict_ob_replay import IctOrderBlockReplayService
from scanner.application.detection.ict_ote_replay import IctOteReplayService
from scanner.application.detection.ict_replay import IctReplayService
from scanner.application.ports.ict_zones import IctZoneRecord
from scanner.shared import Timeframe

SYMBOL = "S6COVUSDT"
TF = Timeframe.M5


class BeyondTheBound(FakeZoneRepository):
    """A zone store in which the seeded zones sit outside the newest 60."""

    def __init__(self, hidden: tuple[IctZoneRecord, ...]) -> None:
        super().__init__()
        self.hidden_ids = {zone.zone_id for zone in hidden}

        for zone in hidden:
            self.zones[zone.zone_id] = zone

    async def list_live(self, symbol, timeframe):
        live = await super().list_live(symbol, timeframe)
        return tuple(zone for zone in live if zone.zone_id not in self.hidden_ids)

    async def list_open(self, symbol, timeframe):
        return await super().list_live(symbol, timeframe)


def recorder(monkeypatch: pytest.MonkeyPatch, cls: type, method: str, result) -> list[str]:
    """Replace one lifecycle with a spy that records which zones it was given."""
    seen: list[str] = []

    async def spy(self, *args, **kwargs):
        record = kwargs.get("record", args[0] if args else None)
        seen.append(record.zone_id)
        return result

    monkeypatch.setattr(cls, method, spy)
    return seen


def candles():
    return pad_for_warmup(fixture_series())


async def run_ict(zones: BeyondTheBound) -> None:
    series = candles()
    await IctReplayService(
        FakeCandleRepository(series),
        zones,
        FakeTransitionRepository(),
        FakeSnapshotStore(),
        FakeClock(),
    ).run(SYMBOL, TF, series[0].open_time, series[-1].close_time)


@pytest.mark.asyncio
async def test_an_fvg_outside_the_scored_60_still_runs_its_lifecycle(monkeypatch) -> None:
    seen = recorder(monkeypatch, IctReplayService, "_replay_fvg_lifecycle", (0, 0))

    await run_ict(BeyondTheBound((zone_record(zone_id="old-fvg", zone_type="FVG", state="OPEN"),)))

    assert "old-fvg" in seen


@pytest.mark.asyncio
async def test_ifvg_and_bpr_outside_the_scored_60_still_run_their_lifecycles(monkeypatch) -> None:
    ifvgs = recorder(monkeypatch, IctReplayService, "_replay_ifvg_lifecycle", 0)
    bprs = recorder(monkeypatch, IctReplayService, "_replay_bpr_lifecycle", 0)

    await run_ict(
        BeyondTheBound(
            (
                zone_record(zone_id="old-ifvg", zone_type="IFVG", state="OPEN"),
                zone_record(zone_id="old-bpr", zone_type="BPR", state="FRESH"),
            )
        )
    )

    assert "old-ifvg" in ifvgs
    assert "old-bpr" in bprs


@pytest.mark.asyncio
async def test_an_ote_outside_the_scored_60_still_runs_its_lifecycle(monkeypatch) -> None:
    seen = recorder(monkeypatch, IctOteReplayService, "_replay_ote_lifecycle", 0)
    series = candles()

    await IctOteReplayService(
        FakeCandleRepository(series),
        BeyondTheBound((zone_record(zone_id="old-ote", zone_type="OTE", state="FRESH"),)),
        FakeTransitionRepository(),
        FakeClock(),
    ).run(SYMBOL, TF, series[0].open_time, series[-1].close_time)

    assert "old-ote" in seen


async def run_ob(zones: BeyondTheBound) -> None:
    series = candles()
    await IctOrderBlockReplayService(
        FakeCandleRepository(series),
        zones,
        FakeTransitionRepository(),
        FakeSnapshotStore(),
        FakeEvidenceRepository(),
        FakeClock(),
    ).run(SYMBOL, TF, series[0].open_time, series[-1].close_time)


@pytest.mark.asyncio
async def test_an_order_block_outside_the_scored_60_still_runs_its_lifecycle(monkeypatch) -> None:
    seen = recorder(monkeypatch, IctOrderBlockReplayService, "_replay_ob_lifecycle", (0, 0, 0))

    await run_ob(BeyondTheBound((zone_record(zone_id="old-ob", zone_type="OB", state="FRESH"),)))

    assert "old-ob" in seen


@pytest.mark.asyncio
async def test_breakers_and_mitigations_outside_the_scored_60_still_run(monkeypatch) -> None:
    breakers = recorder(monkeypatch, IctOrderBlockReplayService, "_replay_breaker_lifecycle", 0)
    mitigations = recorder(
        monkeypatch, IctOrderBlockReplayService, "_replay_mitigation_lifecycle", 0
    )

    await run_ob(
        BeyondTheBound(
            (
                zone_record(zone_id="old-brk", zone_type="BREAKER", state="FRESH"),
                zone_record(zone_id="old-mit", zone_type="MITIGATION", state="FRESH"),
            )
        )
    )

    assert "old-brk" in breakers
    assert "old-mit" in mitigations
