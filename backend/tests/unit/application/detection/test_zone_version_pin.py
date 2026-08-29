"""The version pin on zone reads (2026-08-29's migration-window lesson).

Zone ids carry their algo version, so a version bump re-derives every zone as
a new row while the old generation stays live beside it until it ages out --
observed on the host with 642 s6-v2 FVGs live beside their s6-v3 twins. An
unpinned scoring read counts the same physical gap twice for up to 33 days
(H4). These tests hold the three rules that stop that:

  * scoring and display reads pass `CURRENT_ZONE_VERSIONS`;
  * lifecycle reads pass nothing, so old rows keep aging out;
  * the filter runs BEFORE the bound, so dead versions cannot evict live
    zones from the bounded answer.
"""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from tests.golden.harness.memory import MAX_ZONES, InMemoryIctZoneRepository

from scanner.application.detection.zone_versions import (
    CURRENT_ZONE_VERSIONS,
    assert_covers_all_zone_types,
)
from scanner.application.ports.ict_zones import IctZoneRecord
from scanner.domain.ict.model import ZoneType
from scanner.shared import Timeframe

T0 = datetime(2026, 8, 1, tzinfo=UTC)


def zone(zone_id: str, *, version: str, zone_type: str = "FVG", minute: int = 0) -> IctZoneRecord:
    return IctZoneRecord(
        zone_id=zone_id,
        symbol="BTCUSDT",
        timeframe=Timeframe.H1,
        zone_type=zone_type,
        polarity="BULLISH",
        state="FRESH",
        grade="FVG_B",
        band_low=Decimal("100"),
        band_high=Decimal("106"),
        refined_low=None,
        refined_high=None,
        created_index=5,
        confirmed_index=6,
        created_at=T0 + timedelta(minutes=minute),
        updated_at=T0,
        parent_zone_id=None,
        dealing_range_id=None,
        stale_context=False,
        gap_adjacent=False,
        origin_swept=False,
        evidence=json.dumps({"algo_version": version}),
    )


async def seeded(*zones_: IctZoneRecord) -> InMemoryIctZoneRepository:
    repo = InMemoryIctZoneRepository()

    for record in zones_:
        await repo.upsert(record)

    return repo


async def test_a_pinned_read_sees_one_generation_of_a_gap() -> None:
    current = CURRENT_ZONE_VERSIONS["FVG"]

    repo = await seeded(
        zone("old", version="s6-v2"),
        zone("new", version=current, minute=1),
    )

    live = await repo.list_live("BTCUSDT", Timeframe.H1, only_versions=CURRENT_ZONE_VERSIONS)

    assert [z.zone_id for z in live] == ["new"]


async def test_an_unpinned_read_still_sees_both_because_it_retires_them() -> None:
    """The lifecycle's view. Pin this read too and the superseded rows freeze
    live forever -- invisible to scoring but never leaving the table."""

    repo = await seeded(
        zone("old", version="s6-v2"),
        zone("new", version=CURRENT_ZONE_VERSIONS["FVG"], minute=1),
    )

    live = await repo.list_live("BTCUSDT", Timeframe.H1)

    assert {z.zone_id for z in live} == {"old", "new"}


async def test_the_filter_runs_before_the_bound() -> None:
    """Filtered after the bound, MAX_ZONES stale rows would fill the answer
    and the one current zone would be evicted -- the reader sees an empty
    market because dead versions crowded the doorway."""

    stale = [zone(f"stale-{i}", version="s6-v2", minute=i) for i in range(MAX_ZONES + 5)]

    repo = await seeded(*stale, zone("current", version=CURRENT_ZONE_VERSIONS["FVG"]))

    live = await repo.list_live("BTCUSDT", Timeframe.H1, only_versions=CURRENT_ZONE_VERSIONS)

    assert [z.zone_id for z in live] == ["current"]


async def test_each_zone_type_is_pinned_to_its_own_version() -> None:
    # OB's current version is not FVG's; a single-version filter would drop
    # every type but one.
    repo = await seeded(
        zone("fvg", version=CURRENT_ZONE_VERSIONS["FVG"]),
        zone("ob", version=CURRENT_ZONE_VERSIONS["OB"], zone_type="OB", minute=1),
        zone("ob-old", version="s6-ob-v3", zone_type="OB", minute=2),
    )

    live = await repo.list_live("BTCUSDT", Timeframe.H1, only_versions=CURRENT_ZONE_VERSIONS)

    assert {z.zone_id for z in live} == {"fvg", "ob"}


async def test_unreadable_evidence_is_filtered_not_crashed_on() -> None:
    """A row whose evidence cannot answer the question is not current."""

    broken = replace(zone("broken", version="x"), evidence="not json")

    repo = await seeded(broken, zone("good", version=CURRENT_ZONE_VERSIONS["FVG"], minute=1))

    live = await repo.list_live("BTCUSDT", Timeframe.H1, only_versions=CURRENT_ZONE_VERSIONS)

    assert [z.zone_id for z in live] == ["good"]


def test_the_map_covers_every_zone_type_the_enum_can_produce() -> None:
    """The boot assert, asserted. A type missing from the map is not
    unfiltered -- under a pin it is excluded, which reads as a market without
    those zones."""

    assert_covers_all_zone_types(frozenset(t.value for t in ZoneType))


def test_a_type_the_map_does_not_cover_refuses_to_boot() -> None:
    with pytest.raises(AssertionError, match="NEW_TYPE"):
        assert_covers_all_zone_types(frozenset({"NEW_TYPE"}))
