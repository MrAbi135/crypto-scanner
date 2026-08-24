"""T22 against real PostgreSQL — the constraints and the compare-and-set."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

pytest.importorskip("testcontainers")
from sqlalchemy import text

from scanner.application.identity import SessionService, hash_secret, split_token
from scanner.application.ports.identity import TenantRecord, UserRecord
from scanner.application.ports.sessions import RevokeReason, SessionRecord
from scanner.infrastructure.persistence.database import build_session_factory
from scanner.infrastructure.persistence.identity_repositories import (
    PgTenantRepository,
    PgUserRepository,
)
from scanner.infrastructure.persistence.session_repository import PgSessionRepository

pytestmark = pytest.mark.integration

NOW = datetime(2026, 8, 24, tzinfo=UTC)
USER = "session-test-user"


async def seeded(engine) -> PgSessionRepository:
    sessions = build_session_factory(engine)

    await PgTenantRepository(sessions).upsert(
        TenantRecord(tenant_id="default", name="default", status="ACTIVE", created_at=NOW)
    )

    await PgUserRepository(sessions).create(
        UserRecord(
            user_id=USER,
            tenant_id="default",
            email="sessions@example.com",
            password_hash="$argon2id$placeholder",
            role="user",
            status="ACTIVE",
            created_at=NOW,
        )
    )

    return PgSessionRepository(sessions)


def secret_for(session_id: str, generation: str = "one") -> str:
    """A secret unique to this family.

    T22's unique index is on `refresh_hash` across the whole table, not per
    family — two families on one hash would make the reuse check ambiguous.
    The first version of this file reused "secret-one" across seven families
    and only the first was created; six tests then failed against rows that
    did not exist. Deriving the secret from the id makes that impossible.
    """
    return f"{session_id}-{generation}"


def family(session_id: str, secret: str | None = None, **kw) -> SessionRecord:
    return SessionRecord(
        session_id=session_id,
        user_id=USER,
        refresh_hash=hash_secret(secret or secret_for(session_id)),
        issued_at=NOW,
        rotated_at=NOW,
        expires_at=NOW + timedelta(days=30),
        rotation_count=0,
        **kw,
    )


async def test_a_family_round_trips_and_rotates(engine) -> None:
    repo = await seeded(engine)

    await repo.create(family("f-rot"))

    assert await repo.rotate(
        "f-rot",
        expected_hash=hash_secret(secret_for("f-rot")),
        new_hash=hash_secret(secret_for("f-rot", "two")),
        rotated_at=NOW + timedelta(minutes=1),
    )

    found = await repo.get("f-rot")

    assert found is not None
    assert found.refresh_hash == hash_secret(secret_for("f-rot", "two"))
    assert found.rotation_count == 1


async def test_rotating_with_a_superseded_hash_is_refused(engine) -> None:
    """The predicate that makes reuse detectable rather than duplicable."""
    repo = await seeded(engine)

    await repo.create(family("f-stale"))

    await repo.rotate(
        "f-stale",
        expected_hash=hash_secret(secret_for("f-stale")),
        new_hash=hash_secret(secret_for("f-stale", "two")),
        rotated_at=NOW,
    )

    assert not await repo.rotate(
        "f-stale",
        expected_hash=hash_secret(secret_for("f-stale")),
        new_hash=hash_secret(secret_for("f-stale", "three")),
        rotated_at=NOW,
    )


async def test_concurrent_rotations_of_one_token_produce_one_winner(engine) -> None:
    """Two requests, one valid token, against the real database.

    This is the case a read-then-write cannot get right and a single-threaded
    fake cannot expose. Both statements run against the same row; the
    `refresh_hash = expected` predicate is what makes exactly one of them
    match. Without it both would write, two live tokens would exist for one
    family, and the loser's holder would trip the reuse alarm later —
    revoking a family nobody attacked.
    """
    repo = await seeded(engine)

    await repo.create(family("f-race"))

    results = await asyncio.gather(
        repo.rotate(
            "f-race",
            expected_hash=hash_secret(secret_for("f-race")),
            new_hash=hash_secret(secret_for("f-race", "a")),
            rotated_at=NOW,
        ),
        repo.rotate(
            "f-race",
            expected_hash=hash_secret(secret_for("f-race")),
            new_hash=hash_secret(secret_for("f-race", "b")),
            rotated_at=NOW,
        ),
    )

    assert sorted(results) == [False, True]

    found = await repo.get("f-race")

    assert found is not None
    assert found.rotation_count == 1


async def test_a_revoked_family_cannot_be_rotated_back_into_use(engine) -> None:
    repo = await seeded(engine)

    await repo.create(family("f-dead"))

    assert await repo.revoke("f-dead", reason=RevokeReason.LOGOUT, revoked_at=NOW)

    assert not await repo.rotate(
        "f-dead",
        expected_hash=hash_secret(secret_for("f-dead")),
        new_hash=hash_secret(secret_for("f-dead", "two")),
        rotated_at=NOW,
    )


async def test_the_first_revocation_reason_wins(engine) -> None:
    """A replay against a logged-out family must not become a reported theft."""
    repo = await seeded(engine)

    await repo.create(family("f-reason"))

    assert await repo.revoke("f-reason", reason=RevokeReason.LOGOUT, revoked_at=NOW)
    assert not await repo.revoke("f-reason", reason=RevokeReason.REUSE_DETECTED, revoked_at=NOW)

    found = await repo.get("f-reason")

    assert found is not None
    assert found.revoke_reason == RevokeReason.LOGOUT.value


async def test_two_families_cannot_hold_one_refresh_hash(engine) -> None:
    """T22's unique refresh_hash.

    Two families on one hash would make the reuse check ambiguous, and it is
    the only thing between a stolen token and an indefinite session.
    """
    repo = await seeded(engine)

    assert await repo.create(family("f-uniq-a", "shared-secret"))
    assert not await repo.create(family("f-uniq-b", "shared-secret"))


async def test_a_revoked_row_must_carry_a_reason(engine) -> None:
    """`revoke_reason` is what separates a logout from a detected theft.

    Enforced in the database rather than trusted to callers: a null reason
    turns both into "gone", and the session view and the audit trail both
    read this column.
    """
    repo = await seeded(engine)

    await repo.create(family("f-paired"))

    async with engine.begin() as conn:
        with pytest.raises(Exception, match="ck_sessions_revocation_paired"):
            await conn.execute(
                text("update identity.sessions set revoked_at = :t where session_id = 'f-paired'"),
                {"t": NOW},
            )

    assert (await repo.get("f-paired")) is not None


async def test_an_expired_family_is_not_listed_as_live(engine) -> None:
    """Nothing writes a row when the clock passes `expires_at`.

    A query on `revoked_at IS NULL` alone would show dead families as active
    in §18.1's session list.
    """
    repo = await seeded(engine)

    await repo.create(replace(family("f-expired"), expires_at=NOW + timedelta(minutes=1)))
    await repo.create(family("f-live"))

    live = await repo.list_live_for_user(USER, now=NOW + timedelta(hours=1))

    ids = {s.session_id for s in live}

    assert "f-live" in ids
    assert "f-expired" not in ids


async def test_revoking_all_ends_every_live_family_and_counts_them(engine) -> None:
    repo = await seeded(engine)

    await repo.create(family("f-all-a"))
    await repo.create(family("f-all-b"))
    await repo.revoke("f-all-a", reason=RevokeReason.LOGOUT, revoked_at=NOW)

    # USER_REVOKED rather than PASSWORD_CHANGED: nothing in this piece
    # changes a password, so the latter would be a reason no code path can
    # currently produce.
    ended = await repo.revoke_all_for_user(USER, reason=RevokeReason.USER_REVOKED, revoked_at=NOW)

    # Only the families that were still live -- the already-revoked one keeps
    # its reason and is not double-counted.
    assert ended >= 1

    found = await repo.get("f-all-a")

    assert found is not None
    assert found.revoke_reason == RevokeReason.LOGOUT.value

    assert await repo.list_live_for_user(USER, now=NOW) == ()


async def test_the_service_drives_the_real_table_end_to_end(engine) -> None:
    """Login, rotate, replay — against Postgres rather than a dict."""
    repo = await seeded(engine)
    svc = SessionService(repo)

    issued = await svc.open(USER, now=NOW, device_label="integration")

    assert issued is not None

    rotated = await svc.refresh(issued.token, now=NOW + timedelta(minutes=1))

    assert rotated.ok

    replay = await svc.refresh(issued.token, now=NOW + timedelta(minutes=2))

    assert replay.outcome.value == "REUSE_DETECTED"

    session_id, _ = split_token(issued.token)  # type: ignore[misc]

    found = await repo.get(session_id)

    assert found is not None
    assert found.revoke_reason == RevokeReason.REUSE_DETECTED.value


async def test_a_family_for_an_unknown_user_is_refused(engine) -> None:
    repo = await seeded(engine)

    with pytest.raises(Exception, match="fk_sessions_user"):
        await repo.create(replace(family("f-orphan"), user_id="no-such-user"))
