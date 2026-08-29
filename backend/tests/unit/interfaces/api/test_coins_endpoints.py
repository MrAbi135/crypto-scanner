"""The three doctrine rows the chart draws with (API Spec §18.5)."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from scanner.application.ports.ict_evidence import (
    LiquidityEvidenceRecord,
    StructureEvidenceRecord,
)
from scanner.application.ports.ict_zones import IctZoneRecord
from scanner.application.ports.liquidity_detection import LiquidityPoolRecord
from scanner.interfaces.api.app import build_read_api
from scanner.interfaces.api.envelope import NO_PARAM_SET
from scanner.shared import Timeframe
from tests.unit.interfaces.api.identity_fixtures import bearer, identity

# Every read row is authenticated as of S10-minimal. Minted once at module
# scope: driving `/auth/login` in each test would make every read test also
# a test of Argon2.
NOW = datetime(2026, 8, 17, 12, tzinfo=UTC)
AUTH = bearer(now=NOW)


class FakeClock:
    def now(self) -> datetime:
        return NOW


class FakeEvidence:
    def __init__(self, structure=(), liquidity=()) -> None:
        self.structure = tuple(structure)
        self.liquidity = tuple(liquidity)
        self.windows: list[tuple[datetime, datetime]] = []

    async def list_structure(self, symbol, timeframe, start, end):
        self.windows.append((start, end))
        return self.structure

    async def list_liquidity(self, symbol, timeframe, start, end):
        return self.liquidity


class FakeZones:
    def __init__(self, zones=()) -> None:
        self.zones = tuple(zones)
        self.calls: list[tuple[str, Timeframe]] = []

    async def list_live(self, symbol, timeframe, *, only_versions=None):
        self.calls.append((symbol, timeframe))
        return self.zones


class FakePools:
    def __init__(self, pools=()) -> None:
        self.pools = tuple(pools)

    async def list_active(self, symbol, timeframe):
        return self.pools


class NoCandles:
    def __init__(self, latest: datetime | None = None) -> None:
        self.latest = latest

    async def fetch_series(self, *args):
        return []

    async def latest_open_time(self, *args):
        return self.latest


def build(*, structure=(), liquidity=(), zones=(), pools=(), latest=None):
    evidence = FakeEvidence(structure, liquidity)
    zone_repo = FakeZones(zones)

    app = build_read_api(
        candles=NoCandles(latest),
        evidence=evidence,
        zones=zone_repo,
        pools=FakePools(pools),
        clock=FakeClock(),
        **identity(),
    )

    return TestClient(app), evidence, zone_repo


def structure_event(event_type: str = "BOS_UP") -> StructureEvidenceRecord:
    return StructureEvidenceRecord(
        event_type=event_type,
        event_at=NOW,
        algo_version="s4-v1",
        payload=json.dumps({"swing_index": 12, "level": "62000"}),
    )


def zone(zone_id: str = "z1", *, evidence: str = "{}") -> IctZoneRecord:
    return IctZoneRecord(
        zone_id=zone_id,
        symbol="BTCUSDT",
        timeframe=Timeframe.H1,
        zone_type="FVG",
        polarity="BULLISH",
        state="FRESH",
        grade="A",
        band_low=Decimal("61000"),
        band_high=Decimal("61500.5"),
        refined_low=None,
        refined_high=None,
        created_index=10,
        confirmed_index=12,
        created_at=NOW,
        updated_at=NOW,
        parent_zone_id=None,
        dealing_range_id=None,
        stale_context=False,
        gap_adjacent=False,
        origin_swept=None,
        evidence=evidence,
    )


def pool(pool_id: str = "p1") -> LiquidityPoolRecord:
    return LiquidityPoolRecord(
        pool_id=pool_id,
        symbol="BTCUSDT",
        timeframe=Timeframe.H1,
        side="BSL",
        liquidity_class="EXTERNAL",
        source="SWING",
        price=Decimal("63000"),
        band_low=Decimal("62950"),
        band_high=Decimal("63050"),
        strength=Decimal("74.25"),
        state="ACTIVE",
        member_count=1,
        created_index=8,
        created_at=NOW,
        updated_at=NOW,
        evidence=json.dumps({"strength_components": {"cluster": "6.25", "age": "10"}}),
    )


def sweep() -> LiquidityEvidenceRecord:
    return LiquidityEvidenceRecord(
        pool_id="p1",
        from_state="ACTIVE",
        to_state="SWEPT",
        reason="liquidity_sweep",
        transitioned_at=NOW,
        candle_index=42,
        evidence=json.dumps({"side": "BSL", "liquidity_class": "EXTERNAL"}),
    )


def broken_pool() -> LiquidityEvidenceRecord:
    return LiquidityEvidenceRecord(
        pool_id="p2",
        from_state="ACTIVE",
        to_state="BROKEN",
        reason="close_through",
        transitioned_at=NOW,
        candle_index=43,
        evidence="{}",
    )


@pytest.mark.parametrize(
    "path",
    ["structure", "zones", "liquidity"],
)
def test_every_doctrine_row_declares_its_versions(path: str) -> None:
    """SLS §15.2: doctrine-derived responses carry their provenance."""
    client, _, _ = build()

    body = client.get(
        f"/api/v1/coins/BTCUSDT/{path}",
        params={"timeframe": "H1"},
        headers=AUTH,
    ).json()

    versions = body["meta"]["versions"]

    assert versions["algo_version"]
    assert versions["param_set_version"] == NO_PARAM_SET


def test_the_param_set_sentinel_cannot_be_mistaken_for_a_version() -> None:
    """Param sets are S8. Reporting "1.0.0" would be a false provenance claim.

    Pinned so that when S8 lands and this stops being true, the test fails and
    forces the sentinel out rather than letting it linger as a fake version.
    """
    assert NO_PARAM_SET == "none:pre-s8"
    assert not NO_PARAM_SET[0].isdigit()


def test_structure_returns_recorded_events_with_decoded_evidence() -> None:
    client, _, _ = build(structure=[structure_event(), structure_event("CHOCH_DOWN")])

    body = client.get(
        "/api/v1/coins/BTCUSDT/structure",
        params={"timeframe": "H1"},
        headers=AUTH,
    ).json()

    assert body["page"]["count"] == 2
    assert body["data"][0]["event_type"] == "BOS_UP"

    # Decoded, not handed back as an escaped blob for the client to parse.
    assert body["data"][0]["evidence"] == {"swing_index": 12, "level": "62000"}


def test_the_structure_window_is_window_candles_back_from_the_anchor() -> None:
    """With no stored candles the anchor falls back to the clock."""
    client, evidence, _ = build()

    client.get(
        "/api/v1/coins/BTCUSDT/structure",
        params={"timeframe": "H4", "window": 100},
        headers=AUTH,
    )

    start, end = evidence.windows[0]

    # One step past the anchor so an event on the final bar is inside.
    assert end == NOW + Timeframe.H4.duration
    assert start == NOW - Timeframe.H4.duration * 100


def test_zones_returns_live_zones_with_prices_as_strings() -> None:
    client, _, zone_repo = build(zones=[zone()])

    body = client.get(
        "/api/v1/coins/btcusdt/zones",
        params={"timeframe": "h1"},
        headers=AUTH,
    ).json()

    row = body["data"][0]

    assert row["zone_type"] == "FVG"
    assert row["band_high"] == "61500.5"
    assert isinstance(row["band_low"], str)

    # Normalised before the query reaches the repository.
    assert zone_repo.calls[0] == ("BTCUSDT", Timeframe.H1)


def test_a_zone_with_unreadable_evidence_still_renders() -> None:
    """One corrupt row must not blank a whole chart.

    Unlike the MSS path -- where swallowing a parse failure changed the
    doctrine answer -- here the object itself is intact and only its evidence
    is unreadable. Flagging it lets the chart draw the zone and show that its
    provenance is broken.
    """
    client, _, _ = build(zones=[zone(evidence="{not json")])

    row = client.get(
        "/api/v1/coins/BTCUSDT/zones",
        params={"timeframe": "H1"},
        headers=AUTH,
    ).json()["data"][0]

    assert row["zone_id"] == "z1"
    assert row["evidence"] == {"unreadable": True}


def test_liquidity_ships_strength_with_its_components() -> None:
    """SLS §15.4: there is no representation of a bare score."""
    client, _, _ = build(pools=[pool()])

    body = client.get(
        "/api/v1/coins/BTCUSDT/liquidity",
        params={"timeframe": "H1"},
        headers=AUTH,
    ).json()

    strength = body["data"]["pools"][0]["strength"]

    assert strength["score"] == "74.25"
    assert strength["components"] == {"cluster": "6.25", "age": "10"}


def test_only_sweeps_appear_under_sweeps() -> None:
    """A pool broken by a close-through is not a sweep (SLS §4.6).

    The transition feed carries both, and conflating them is the single
    hardest call in the liquidity doctrine -- so the filter is asserted rather
    than assumed.
    """
    client, _, _ = build(liquidity=[sweep(), broken_pool()])

    body = client.get(
        "/api/v1/coins/BTCUSDT/liquidity",
        params={"timeframe": "H1"},
        headers=AUTH,
    ).json()

    sweeps = body["data"]["sweeps"]

    assert len(sweeps) == 1
    assert sweeps[0]["pool_id"] == "p1"
    assert sweeps[0]["to_state"] == "SWEPT"


@pytest.mark.parametrize("path", ["structure", "zones", "liquidity"])
def test_an_unknown_timeframe_is_refused_on_every_row(path: str) -> None:
    client, _, _ = build()

    response = client.get(
        f"/api/v1/coins/BTCUSDT/{path}",
        params={"timeframe": "M3"},
        headers=AUTH,
    )

    assert response.status_code == 400
    assert response.json()["error"]["details"][0]["field"] == "timeframe"


def test_the_window_reaches_past_the_final_candles_close() -> None:
    """An event on the last bar must not fall one instant outside the window.

    A candle is keyed by open_time, so `lastOpen + duration` includes it. A
    transition is stamped at the *close* of the candle that caused it, which is
    exactly `lastOpen + duration` -- and the repository filters
    `transitioned_at < end`. The two bounds agree everywhere except the final
    bar, where the most recent event is the one most likely being looked for.

    Found on the GOLDENSWEEP dataset: last candle open 06:00, sweep stamped
    07:00, window ending 07:00, and zero sweeps returned for a dataset whose
    entire purpose is that sweep.
    """
    last_open = datetime(2026, 1, 5, 6, tzinfo=UTC)

    client, evidence, _ = build(latest=last_open)

    client.get("/api/v1/coins/GOLDENSWEEP/structure", params={"timeframe": "H1"}, headers=AUTH)

    _, end = evidence.windows[0]

    assert end > last_open + Timeframe.H1.duration
