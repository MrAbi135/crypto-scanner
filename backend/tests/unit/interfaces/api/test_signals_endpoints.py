"""§18.8's per-signal rows, driven through a real FastAPI app."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from scanner.application.identity import AccountService, SessionService
from scanner.application.identity.tokens import AccessTokens
from scanner.application.ports.signal_outcomes import SignalOutcomeRecord
from scanner.application.ports.signal_transitions import SignalTransitionRecord
from scanner.application.ports.signals import SignalRecord
from scanner.application.signal_audit import reseal
from scanner.interfaces.api.app import build_read_api
from scanner.shared import Timeframe
from tests.unit.interfaces.api.identity_fixtures import (
    TEST_SECRET,
    FakeSessionStore,
    FakeTenants,
    FakeUsers,
    bearer,
)

NOW = datetime(2026, 8, 25, 12, tzinfo=UTC)
PUBLISHED = datetime(2026, 8, 24, 9, tzinfo=UTC)
AUTH = bearer(now=NOW)

PAYLOAD = {
    "symbol": "BTCUSDT",
    "evidence": {
        "structure": [{"event_id": "ev-1", "candle_open_time": "2026-08-24T08:00:00+00:00"}],
        "liquidity": [{"pool_id": "p-1"}],
    },
    "confidence": {"final": "82", "factors": {"F1": "12"}},
    "versions": {"algo_version": "s8-test", "param_set_version": "2026.08.24.2"},
}

SEALED = json.dumps(PAYLOAD, sort_keys=True, separators=(",", ":"))


class FakeClock:
    def now(self) -> datetime:
        return NOW


class EmptyRepo:
    async def fetch_series(self, *a, **k):
        return []

    async def latest_open_time(self, *a, **k):
        return None

    async def list_between(self, *a, **k):
        return ()

    async def list_live(self, *a, **k):
        return ()

    async def list_active(self, *a, **k):
        return ()


def signal(*, payload: str = SEALED, payload_hash: str | None = None) -> SignalRecord:
    return SignalRecord(
        signal_id="sig-1",
        setup_id="sig-1",
        symbol="BTCUSDT",
        timeframe=Timeframe.H1,
        direction="UP",
        archetype="A4",
        grade="A",
        final_confidence=Decimal(82),
        entry_proximal=Decimal(104),
        entry_distal=Decimal(100),
        invalidation_level=Decimal(98),
        target_bands=json.dumps({"primary": {"low": "112", "high": "114"}}),
        published_at=PUBLISHED,
        ttl_candles=24,
        algo_version="s8-test",
        param_set_version="2026.08.24.2",
        payload=payload,
        payload_hash=payload_hash if payload_hash is not None else reseal(payload),
        dedup_key="BTCUSDT|H1|UP|A4|104.00:100.00",
    )


class FakeSignals:
    def __init__(self, rows: list[SignalRecord] | None = None) -> None:
        self.rows = {r.signal_id: r for r in (rows if rows is not None else [signal()])}

    async def get(self, signal_id):
        return self.rows.get(signal_id)


class FakeTransitions:
    def __init__(self, rows=(), state: str | None = "ACTIVE") -> None:
        self.rows = tuple(rows)
        self.state = state

    async def current_state(self, signal_id):
        return self.state

    async def list_for_signal(self, signal_id):
        return self.rows


class FakeOutcomes:
    def __init__(self, row: SignalOutcomeRecord | None = None) -> None:
        self.row = row

    async def get(self, signal_id):
        return self.row


def transition(
    *,
    from_state: str,
    to_state: str,
    hour: int,
    stress_test: bool = False,
    refresh: bool = False,
    evidence: str = '{"reason": "entry zone touched"}',
) -> SignalTransitionRecord:
    at = PUBLISHED.replace(hour=hour)

    return SignalTransitionRecord(
        transition_id=f"t-{hour}-{int(refresh)}",
        signal_id="sig-1",
        from_state=from_state,
        to_state=to_state,
        at_candle_open_time=at,
        recorded_at=at,
        stress_test=stress_test,
        refresh=refresh,
        trigger_evidence=evidence,
    )


def build(*, signals=None, transitions=None, outcomes=None) -> TestClient:
    store = FakeSessionStore()

    app = build_read_api(
        candles=EmptyRepo(),
        evidence=EmptyRepo(),
        zones=EmptyRepo(),
        pools=EmptyRepo(),
        clock=FakeClock(),
        accounts=AccountService(FakeUsers(), FakeTenants()),
        sessions=SessionService(store),
        session_repository=store,
        access_tokens=AccessTokens(TEST_SECRET),
        signals=signals or FakeSignals(),
        signal_transitions=transitions or FakeTransitions(),
        outcomes=outcomes or FakeOutcomes(),
    )

    return TestClient(app)


# ------------------------------------------------------------------- detail


def test_the_summary_projection_omits_the_sealed_payload() -> None:
    body = build().get("/api/v1/signals/sig-1", headers=AUTH).json()

    data = body["data"]

    assert data["signal_id"] == "sig-1"
    assert data["grade"] == "A"
    assert data["lifecycle_state"] == "ACTIVE"
    # §18.8: "`full` includes sealed payload fields". Summary does not.
    assert "payload" not in data
    assert "payload_hash" not in data


def test_the_full_projection_carries_the_payload_and_verifies_its_seal() -> None:
    """§15.3(5) puts the hash on the signal "for audit".

    An audit value nobody ever checks is a column. Recomputed here so a client
    does not have to reproduce our canonical JSON to verify it.
    """
    body = (
        build()
        .get(
            "/api/v1/signals/sig-1",
            params={"projection": "full"},
            headers=AUTH,
        )
        .json()
    )

    data = body["data"]

    assert data["payload"]["symbol"] == "BTCUSDT"
    assert data["payload_hash"]
    assert data["payload_hash_verified"] is True


def test_a_tampered_payload_reports_its_seal_as_broken_rather_than_failing() -> None:
    """The row still renders, and says the hash does not match.

    Refusing to serve it would hide the tampering behind an outage; serving it
    silently would present edited evidence as sealed.
    """
    rows = FakeSignals([signal(payload='{"symbol":"ETHUSDT"}', payload_hash="a" * 64)])

    body = (
        build(signals=rows)
        .get(
            "/api/v1/signals/sig-1",
            params={"projection": "full"},
            headers=AUTH,
        )
        .json()
    )

    assert body["data"]["payload_hash_verified"] is False


def test_a_resolved_signal_shows_its_outcome() -> None:
    """§15.4: "Expired signals display as expired, failed as failed"."""

    resolved = SignalOutcomeRecord(
        signal_id="sig-1",
        outcome="FAILED",
        resolved_at=NOW,
        elapsed_candles=7,
        mfe_r=Decimal("1.25"),
        mae_r=Decimal("1.00"),
        excluded_from_stats=False,
        resolution_evidence="{}",
    )

    body = build(outcomes=FakeOutcomes(resolved)).get("/api/v1/signals/sig-1", headers=AUTH).json()

    assert body["data"]["outcome"]["outcome"] == "FAILED"
    assert body["data"]["outcome"]["mfe_r"] == "1.25"


def test_a_live_signal_carries_no_outcome_key_at_all() -> None:
    """Absent, not null-with-a-shape.

    An outcome object full of nulls reads as "resolved to nothing", which is a
    state §12 does not have.
    """
    assert "outcome" not in build().get("/api/v1/signals/sig-1", headers=AUTH).json()["data"]


def test_the_lifecycle_state_is_passed_through_rather_than_defaulted() -> None:
    """§12.2 writes the PUBLISHED transition with the signal, so a missing
    state means a write did not land. Defaulting would turn that into a signal
    that looks fine."""

    body = (
        build(transitions=FakeTransitions(state=None))
        .get("/api/v1/signals/sig-1", headers=AUTH)
        .json()
    )

    assert body["data"]["lifecycle_state"] is None


def test_freshness_is_the_publication_moment_not_now() -> None:
    """A sealed snapshot is not a live reading.

    `observed_at: now` would let a client render a three-day-old signal as
    freshly observed, which is exactly §2.12's staleness concern.
    """
    body = build().get("/api/v1/signals/sig-1", headers=AUTH).json()

    assert body["meta"]["freshness"]["observed_at"] == PUBLISHED.isoformat()


@pytest.mark.parametrize(
    "path",
    ["/api/v1/signals/nope", "/api/v1/signals/nope/evidence", "/api/v1/signals/nope/transitions"],
)
def test_an_unknown_signal_is_a_404_on_every_row(path: str) -> None:
    response = build().get(path, headers=AUTH)

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"


@pytest.mark.parametrize(
    "path",
    [
        "/api/v1/signals/sig-1",
        "/api/v1/signals/sig-1/evidence",
        "/api/v1/signals/sig-1/transitions",
    ],
)
def test_every_signal_row_needs_a_token(path: str) -> None:
    assert build().get(path).status_code == 401


# ----------------------------------------------------------------- evidence


def test_the_evidence_chain_comes_from_the_sealed_payload() -> None:
    """§12.1 froze it at publication.

    A chart deep-links to the candles a claim was made from, which needs the
    ids that were true then — not the ones a fresh pass would produce now.
    """
    body = build().get("/api/v1/signals/sig-1/evidence", headers=AUTH).json()

    chain = body["data"]["evidence"]

    assert chain["structure"][0]["event_id"] == "ev-1"
    assert chain["structure"][0]["candle_open_time"] == "2026-08-24T08:00:00+00:00"
    # The natural keys a deep-link needs travel with it: an event id alone
    # locates nothing without its series.
    assert body["data"]["symbol"] == "BTCUSDT"
    assert body["data"]["timeframe"] == "H1"


# --------------------------------------------------------------- transitions


def test_the_lifecycle_row_includes_stress_tests_and_refreshes() -> None:
    """§18.8 names stress tests; §10.3's refreshes are the same kind of fact.

    A history that dropped either would show a quiet life for a signal that
    was tested three times and re-detected forty.
    """
    rows = FakeTransitions(
        [
            transition(from_state="DETECTED", to_state="PUBLISHED", hour=9),
            transition(from_state="PUBLISHED", to_state="PUBLISHED", hour=10, refresh=True),
            transition(from_state="PUBLISHED", to_state="ACTIVE", hour=11),
            transition(from_state="ACTIVE", to_state="ACTIVE", hour=12, stress_test=True),
        ]
    )

    body = build(transitions=rows).get("/api/v1/signals/sig-1/transitions", headers=AUTH).json()

    data = body["data"]

    assert body["page"]["count"] == 4
    assert [r["to_state"] for r in data] == ["PUBLISHED", "PUBLISHED", "ACTIVE", "ACTIVE"]
    assert data[1]["refresh"] is True
    assert data[3]["stress_test"] is True
    assert data[0]["evidence"]["reason"] == "entry zone touched"


def test_an_unparseable_evidence_blob_does_not_take_the_history_down() -> None:
    """The other rows are still true.

    A 500 on one malformed blob would hide the nine good ones; returning it as
    an empty object would make a broken row read as an observation with nothing
    to say.
    """
    rows = FakeTransitions(
        [
            transition(from_state="DETECTED", to_state="PUBLISHED", hour=9, evidence="{not json"),
            transition(from_state="PUBLISHED", to_state="ACTIVE", hour=10),
        ]
    )

    body = build(transitions=rows).get("/api/v1/signals/sig-1/transitions", headers=AUTH).json()

    assert body["page"]["count"] == 2
    assert "unparseable" in body["data"][0]["evidence"]
    assert body["data"][1]["evidence"]["reason"] == "entry zone touched"


def test_an_empty_history_reports_no_observation_time() -> None:
    body = (
        build(transitions=FakeTransitions([]))
        .get("/api/v1/signals/sig-1/transitions", headers=AUTH)
        .json()
    )

    assert body["page"]["count"] == 0
    assert body["meta"]["freshness"].get("observed_at") is None
