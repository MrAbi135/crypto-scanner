"""§18.8's per-signal rows, driven through a real FastAPI app."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
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
    EmptyFeed,
    EmptyIncidents,
    EmptyRankings,
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


class FakeArchive:
    """§18.8's archive, in memory.

    Applies the filters itself rather than returning everything: a fake that
    ignored them would let an endpoint that forgot to pass them through pass
    its tests.
    """

    def __init__(self, rows=()) -> None:
        self.rows = tuple(rows)
        self.seen_filters = None
        self.seen_after = None

    async def history(self, filters, *, limit, after=None):
        from scanner.application.ports.track_record import HistoryPage

        self.seen_filters = filters
        self.seen_after = after

        kept = [
            row
            for row in self.rows
            if (not filters.grades or row.signal.grade in filters.grades)
            and (not filters.archetypes or row.signal.archetype in filters.archetypes)
            and (not filters.outcomes or row.outcome in filters.outcomes)
            and (
                filters.published_from is None or row.signal.published_at >= filters.published_from
            )
            and (filters.published_to is None or row.signal.published_at < filters.published_to)
        ]

        if after is not None:
            kept = [r for r in kept if r.signal.signal_id < after["signal_id"]]

        page = kept[:limit]
        more = len(kept) > limit

        return HistoryPage(
            rows=tuple(page),
            next_position=(
                {
                    "published_at": page[-1].signal.published_at.isoformat(),
                    "signal_id": page[-1].signal.signal_id,
                }
                if more and page
                else None
            ),
        )


class FakeStatistics:
    """§18.8's aggregate, in memory.

    Returns whatever counts it was given and records the arguments, so a test
    can assert what the endpoint asked for as well as what it rendered.
    """

    def __init__(self, rows=()) -> None:
        self.rows = tuple(rows)
        self.seen_group_by = None
        self.seen_since = None

    async def outcome_counts(self, *, group_by, since=None, until=None):
        self.seen_group_by = group_by
        self.seen_since = since

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


def build(*, signals=None, transitions=None, outcomes=None, archive=None, stats=None) -> TestClient:
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
        track_record=archive or FakeArchive(),
        track_statistics=stats or FakeStatistics(),
        rankings=EmptyRankings(),
        feed=EmptyFeed(),
        incidents=EmptyIncidents(),
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


# ------------------------------------------------------------------- history


def archived(
    signal_id: str,
    *,
    hour: int,
    grade: str = "A",
    archetype: str = "A4",
    outcome: str | None = None,
    excluded: bool = False,
):
    from scanner.application.ports.track_record import ArchivedSignal

    row = signal()
    row = type(row)(
        **{
            **{f: getattr(row, f) for f in row.__slots__},
            "signal_id": signal_id,
            "grade": grade,
            "archetype": archetype,
            "published_at": PUBLISHED.replace(hour=hour),
        }
    )

    if outcome is None:
        return ArchivedSignal(signal=row)

    return ArchivedSignal(
        signal=row,
        outcome=outcome,
        resolved_at=PUBLISHED.replace(hour=hour + 1),
        elapsed_candles=4,
        mfe_r=Decimal("1.50"),
        mae_r=Decimal("0.40"),
        excluded_from_stats=excluded,
    )


def test_the_archive_holds_live_and_resolved_signals_alike() -> None:
    """The archive is every published signal, not the closed ones.

    An inner join would quietly turn "the archive" into "the trades that
    finished", which is the flattering half.
    """
    rows = [
        archived("s-2", hour=10, outcome="SUCCESS"),
        archived("s-1", hour=9),
    ]

    body = build(archive=FakeArchive(rows)).get("/api/v1/signals/history", headers=AUTH).json()

    assert body["page"]["count"] == 2
    assert body["data"][0]["outcome"]["outcome"] == "SUCCESS"
    # Live: the key is absent, not null-with-a-shape. A null MFE beside a real
    # one invites a client to chart it as zero.
    assert "outcome" not in body["data"][1]


def test_an_archived_outcome_says_whether_statistics_will_count_it() -> None:
    """PRD FC-10.1: "Delisting-expired signals excluded from quality stats but
    present in archive"."""

    rows = [archived("s-1", hour=9, outcome="EXPIRED_ACTIVE", excluded=True)]

    body = build(archive=FakeArchive(rows)).get("/api/v1/signals/history", headers=AUTH).json()

    assert body["data"][0]["outcome"]["excluded_from_stats"] is True


def test_the_history_filters_reach_the_repository() -> None:
    """A filter parsed and not passed on is the same lie §9 forbids.

    Asserted against what the repository was handed, not against the rows that
    came back — a fake that filtered correctly would hide an endpoint that
    dropped the filters on the floor.
    """
    archive = FakeArchive([archived("s-1", hour=9, grade="A")])

    build(archive=archive).get(
        "/api/v1/signals/history",
        params={
            "filter[grade][in]": "A,S",
            "filter[archetype]": "A4",
            "filter[published_at][gte]": "2026-08-24T00:00:00+00:00",
            "filter[published_at][lte]": "2026-08-26",
        },
        headers=AUTH,
    )

    seen = archive.seen_filters

    assert seen.grades == ("A", "S")
    assert seen.archetypes == ("A4",)
    assert seen.published_from == datetime(2026, 8, 24, tzinfo=UTC)
    # A bare date is read as UTC midnight rather than refused: the platform is
    # UTC throughout, and rejecting the common form would make it the awkward
    # one.
    assert seen.published_to == datetime(2026, 8, 26, tzinfo=UTC)


def test_an_unknown_history_filter_is_a_422() -> None:
    response = build().get(
        "/api/v1/signals/history",
        params={"filter[colour]": "red"},
        headers=AUTH,
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "SEMANTIC_REJECTION"


def test_a_non_iso_date_filter_names_the_parameter_that_was_wrong() -> None:
    response = build().get(
        "/api/v1/signals/history",
        params={"filter[published_at][gte]": "last tuesday"},
        headers=AUTH,
    )

    assert response.status_code == 422
    assert response.json()["error"]["details"][0]["field"] == "filter[published_at][gte]"


def test_the_page_carries_a_cursor_only_when_there_is_more() -> None:
    rows = [archived(f"s-{i}", hour=9) for i in range(5)]

    client = build(archive=FakeArchive(rows))

    full = client.get("/api/v1/signals/history", params={"limit": "2"}, headers=AUTH).json()

    assert full["page"]["has_more"] is True
    assert full["page"]["next_cursor"]

    last = client.get("/api/v1/signals/history", params={"limit": "50"}, headers=AUTH).json()

    assert last["page"]["has_more"] is False
    assert last["page"]["next_cursor"] is None


def test_a_cursor_round_trips_through_the_endpoint() -> None:
    rows = [archived(f"s-{i}", hour=9) for i in range(5)]

    archive = FakeArchive(rows)
    client = build(archive=archive)

    first = client.get("/api/v1/signals/history", params={"limit": "2"}, headers=AUTH).json()

    client.get(
        "/api/v1/signals/history",
        params={"limit": "2", "cursor": first["page"]["next_cursor"]},
        headers=AUTH,
    )

    # The repository was handed the decoded position, not the opaque string.
    assert archive.seen_after["signal_id"] == first["data"][-1]["signal_id"]


def test_an_invalid_cursor_is_refused_rather_than_silently_restarted() -> None:
    """Restarting at page one would make a paginating client loop forever
    without ever reporting an error."""

    response = build().get(
        "/api/v1/signals/history",
        params={"cursor": "not-a-real-cursor"},
        headers=AUTH,
    )

    assert response.status_code == 422
    assert response.json()["error"]["details"][0]["field"] == "cursor"


def test_the_history_row_is_matched_before_the_signal_id_row() -> None:
    """FastAPI matches in declaration order.

    Declared after `/{signal_id}`, "history" would be swallowed as an id and
    answer 404 — and the same trap waits for `/statistics`.
    """
    assert build().get("/api/v1/signals/history", headers=AUTH).status_code == 200


def test_history_needs_a_token_like_everything_else() -> None:
    assert build().get("/api/v1/signals/history").status_code == 401


# ---------------------------------------------------------------- statistics


def counts(
    *,
    version: str = "s8-v1",
    key: str | None = "A4",
    successes: int = 0,
    failures: int = 0,
    expired: int = 0,
    invalidated: int = 0,
):
    from scanner.application.ports.track_record import OutcomeCounts

    return OutcomeCounts(
        algo_version=version,
        key=key,
        successes=successes,
        failures=failures,
        expired=expired,
        invalidated=invalidated,
    )


def statistics(client, **params):
    return client.get("/api/v1/signals/statistics", params=params, headers=AUTH).json()


def test_a_group_reports_its_counts_its_rate_and_what_the_rate_is_worth() -> None:
    rows = [counts(successes=30, failures=10, expired=5, invalidated=2)]

    group = statistics(build(stats=FakeStatistics(rows)))["data"][0]

    assert group["counts"] == {
        "resolved": 47,
        "success": 30,
        "failed": 10,
        "expired": 5,
        "invalidated_early": 2,
    }
    assert group["hit_rate"]["rated"] == 40
    assert group["hit_rate"]["rate_pct"] == "75.00"
    assert group["hit_rate"]["sufficient_for_inference"] is True
    assert group["hit_rate"]["label"] == "n=40"


def test_expired_signals_are_reported_and_kept_out_of_the_rate() -> None:
    """§12.4: "a scanner that times out constantly has a target-selection
    problem — visible, not hidden"."""

    rows = [counts(successes=4, failures=1, expired=95)]

    group = statistics(build(stats=FakeStatistics(rows)))["data"][0]

    assert group["counts"]["expired"] == 95
    assert group["counts"]["resolved"] == 100
    # Five rated, not a hundred.
    assert group["hit_rate"]["rated"] == 5
    assert group["hit_rate"]["rate_pct"] == "80.00"


def test_a_small_sample_carries_its_interval_and_says_so() -> None:
    """PRD FC-10.1: "n=14 — insufficient for inference"."""

    rows = [counts(successes=10, failures=4)]

    rate = statistics(build(stats=FakeStatistics(rows)))["data"][0]["hit_rate"]

    assert rate["label"] == "n=14 — insufficient for inference"
    assert rate["sufficient_for_inference"] is False
    # The numbers are still returned: the flag is a label, not a gate.
    assert rate["rate_pct"] == "71.43"
    assert rate["confidence_interval"]["level"] == "95%"
    assert float(rate["confidence_interval"]["low_pct"]) < 71.43
    assert float(rate["confidence_interval"]["high_pct"]) > 71.43


def test_an_unbroken_run_does_not_report_certainty() -> None:
    """The reason the interval is Wilson's and not the textbook one."""

    rows = [counts(successes=9)]

    interval = statistics(build(stats=FakeStatistics(rows)))["data"][0]["hit_rate"][
        "confidence_interval"
    ]

    assert interval["high_pct"] == "100.00"
    assert float(interval["low_pct"]) < 80


def test_a_group_with_nothing_rated_has_a_null_rate_not_a_zero() -> None:
    """Zero is a claim from no evidence, and a client would chart it."""

    rows = [counts(expired=12)]

    rate = statistics(build(stats=FakeStatistics(rows)))["data"][0]["hit_rate"]

    assert rate["rate_pct"] is None
    assert rate["confidence_interval"] is None
    assert rate["label"] == "n=0 — no rated outcomes yet"


def test_every_group_carries_its_version_whatever_the_axis() -> None:
    """§18.8: "Version-segmented always".

    A hit rate averaged over two algorithm versions is the average of two
    different scanners, and the number describes neither.
    """
    rows = [
        counts(version="s8-v1", key="A4", successes=10, failures=10),
        counts(version="s8-v2", key="A4", successes=18, failures=2),
    ]

    groups = statistics(build(stats=FakeStatistics(rows)), group_by="archetype")["data"]

    assert [g["algo_version"] for g in groups] == ["s8-v1", "s8-v2"]
    assert {g["key"] for g in groups} == {"A4"}
    # Not merged into one 70% row.
    assert [g["hit_rate"]["rate_pct"] for g in groups] == ["50.00", "90.00"]


def test_grouping_by_version_leaves_the_key_null() -> None:
    """The value is already in `algo_version`; repeating it would invite a
    client to render the same string twice."""

    rows = [counts(version="s8-v1", key=None, successes=5, failures=5)]

    group = statistics(build(stats=FakeStatistics(rows)), group_by="version")["data"][0]

    assert group["key"] is None
    assert group["algo_version"] == "s8-v1"


@pytest.mark.parametrize("axis", ["archetype", "grade", "timeframe", "version"])
def test_every_documented_axis_is_accepted(axis: str) -> None:
    stats = FakeStatistics()

    statistics(build(stats=stats), group_by=axis)

    assert stats.seen_group_by.value == axis


def test_an_unknown_axis_is_refused() -> None:
    response = build().get(
        "/api/v1/signals/statistics",
        params={"group_by": "colour"},
        headers=AUTH,
    )

    assert response.status_code == 422


def test_the_window_narrows_the_range_and_all_means_everything() -> None:
    stats = FakeStatistics()
    client = build(stats=stats)

    statistics(client, window="30d")

    assert stats.seen_since == NOW - timedelta(days=30)

    statistics(client, window="all")

    # The unwindowed view is the default and the honest starting point:
    # Constitution §28.6 makes the whole record the claim.
    assert stats.seen_since is None


def test_an_unknown_window_is_refused_with_the_options_named() -> None:
    """A free-form duration would let "3m" mean 90 or 92 days depending on the
    calendar, and a track record that moves with the month is one nobody can
    reproduce."""

    response = build().get(
        "/api/v1/signals/statistics",
        params={"window": "3m"},
        headers=AUTH,
    )

    assert response.status_code == 422

    body = response.json()

    assert body["error"]["details"][0]["field"] == "window"
    assert "90d" in body["error"]["message"]


def test_the_statistics_row_is_matched_before_the_signal_id_row() -> None:
    assert build().get("/api/v1/signals/statistics", headers=AUTH).status_code == 200


def test_statistics_needs_a_token() -> None:
    assert build().get("/api/v1/signals/statistics").status_code == 401
