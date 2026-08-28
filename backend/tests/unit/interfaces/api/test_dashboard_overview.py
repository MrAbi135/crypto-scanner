"""§18.3's hub, restricted to what is measurable (Blueprint §21.6)."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from fastapi.testclient import TestClient

from scanner.application.feed import Feed, FeedRow
from scanner.application.identity import AccountService, SessionService
from scanner.application.identity.tokens import AccessTokens
from scanner.application.ports.ict_evidence import RecentSweepRecord
from scanner.application.ports.signals import SignalRecord
from scanner.interfaces.api.app import build_read_api
from scanner.shared import Timeframe
from tests.unit.interfaces.api.identity_fixtures import (
    TEST_SECRET,
    EmptyFeed,
    EmptyIncidents,
    EmptyRankings,
    EmptySignals,
    EmptySymbols,
    FakeSessionStore,
    FakeTenants,
    FakeUsers,
    bearer,
)

NOW = datetime(2026, 8, 28, 12, 0, tzinfo=UTC)
AUTH = bearer(now=NOW)


class FakeClock:
    def now(self) -> datetime:
        return NOW


def signal(signal_id: str, symbol: str) -> SignalRecord:
    return SignalRecord(
        signal_id=signal_id,
        setup_id=f"setup-{signal_id}",
        dedup_key=f"dk-{signal_id}",
        symbol=symbol,
        timeframe=Timeframe.H1,
        direction="UP",
        archetype="A3",
        grade="B",
        final_confidence=Decimal("75"),
        entry_proximal=Decimal("101"),
        entry_distal=Decimal("100"),
        invalidation_level=Decimal("99"),
        target_bands='{"primary": null, "secondary": null}',
        ttl_candles=24,
        published_at=NOW,
        payload="{}",
        payload_hash="h",
        algo_version="a",
        param_set_version="p",
    )


class BusyFeed(EmptyFeed):
    def __init__(self, count: int) -> None:
        self._count = count

    async def read(self) -> Feed:
        return Feed(
            generated_at=NOW,
            rows=tuple(
                FeedRow(
                    position=index + 1,
                    signal=signal(f"sig-{index + 1}", "ETHUSDT"),
                    lifecycle_state="PUBLISHED",
                    display=Decimal("70"),
                    elapsed_candles=1,
                )
                for index in range(self._count)
            ),
            live_total=self._count,
        )


class SweepEvidence(EmptySignals):
    def __init__(self, rows=()) -> None:
        self._rows = tuple(rows)
        self.asked_limit: int | None = None

    async def list_recent_sweeps(self, *, limit):
        self.asked_limit = limit

        return self._rows[:limit]


def sweep(pool_id: str, side: str | None = "BSL") -> RecentSweepRecord:
    return RecentSweepRecord(
        symbol="BTCUSDT",
        timeframe=Timeframe.M15,
        pool_id=pool_id,
        side=side,
        to_state="SWEPT",
        reason="single_candle_sweep",
        transitioned_at=NOW,
        evidence="{}",
    )


def build(feed=None, evidence=None) -> TestClient:
    store = FakeSessionStore()

    return TestClient(
        build_read_api(
            candles=EmptySignals(),
            evidence=evidence or SweepEvidence(),
            zones=EmptySignals(),
            pools=EmptySignals(),
            clock=FakeClock(),
            accounts=AccountService(FakeUsers(), FakeTenants()),
            sessions=SessionService(store),
            session_repository=store,
            access_tokens=AccessTokens(TEST_SECRET),
            signals=EmptySignals(),
            signal_transitions=EmptySignals(),
            outcomes=EmptySignals(),
            track_record=EmptySignals(),
            track_statistics=EmptySignals(),
            rankings=EmptyRankings(),
            feed=feed or EmptyFeed(),
            incidents=EmptyIncidents(),
            symbols=EmptySymbols(),
        )
    )


def get(client: TestClient):
    return client.get("/api/v1/dashboard/overview", headers=AUTH)


def test_the_head_is_the_boards_own_order_cut_at_five() -> None:
    """Not a second ranking. A "top" computed here would eventually disagree
    with the feed about what is on top."""

    body = get(build(feed=BusyFeed(8))).json()["data"]

    assert [row["rank"] for row in body["top_signals"]] == [1, 2, 3, 4, 5]
    assert body["live_total"] == 8


def test_a_short_board_serves_what_it_has() -> None:
    body = get(build(feed=BusyFeed(2))).json()["data"]

    assert len(body["top_signals"]) == 2
    assert body["live_total"] == 2


def test_recent_sweeps_carry_their_context_and_side() -> None:
    evidence = SweepEvidence([sweep("p1")])

    body = get(build(evidence=evidence)).json()["data"]

    row = body["recent_sweeps"][0]

    assert (row["symbol"], row["timeframe"], row["side"]) == ("BTCUSDT", "M15", "BSL")
    assert evidence.asked_limit == 10


def test_a_sweep_whose_pool_is_gone_lists_with_no_side() -> None:
    """The transitions table is append-only so the record outlives the object.
    A vanished pool must not erase the sweep, and its side must not be
    guessed."""

    body = get(build(evidence=SweepEvidence([sweep("p1", side=None)]))).json()["data"]

    assert body["recent_sweeps"][0]["side"] is None


def test_what_the_hub_cannot_measure_is_named() -> None:
    joined = " ".join(get(build()).json()["data"]["not_measured"])

    assert "regime" in joined
    assert "compression" in joined
    assert "watchlist" in joined


def test_it_needs_a_token() -> None:
    assert build().get("/api/v1/dashboard/overview").status_code == 401
