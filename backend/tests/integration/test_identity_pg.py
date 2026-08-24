"""T20/T21 against real PostgreSQL."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime

import pytest

pytest.importorskip("testcontainers")
from sqlalchemy import text

from scanner.application.identity import AccountService, fold_email, user_id_for
from scanner.application.ports.identity import TenantRecord, UserRecord
from scanner.infrastructure.persistence.database import build_session_factory
from scanner.infrastructure.persistence.identity_repositories import (
    PgTenantRepository,
    PgUserRepository,
)

pytestmark = pytest.mark.integration

NOW = datetime(2026, 8, 24, tzinfo=UTC)
GOOD = "correct-horse-battery-staple"


def user(email: str, *, user_id: str | None = None, **kw) -> UserRecord:
    folded = fold_email(email)

    return UserRecord(
        user_id=user_id or user_id_for(folded),
        tenant_id="default",
        email=folded,
        password_hash="$argon2id$v=19$m=65536,t=3,p=1$c2FsdHNhbHQ$" + "a" * 43,
        role="user",
        status="ACTIVE",
        created_at=NOW,
        **kw,
    )


async def seeded(engine) -> tuple[PgUserRepository, PgTenantRepository]:
    sessions = build_session_factory(engine)
    tenants = PgTenantRepository(sessions)

    await tenants.upsert(
        TenantRecord(tenant_id="default", name="default", status="ACTIVE", created_at=NOW)
    )

    return PgUserRepository(sessions), tenants


async def test_an_account_round_trips(engine) -> None:
    users, _ = await seeded(engine)

    assert await users.create(user("ops@example.com"))

    found = await users.get_by_email("ops@example.com")

    assert found is not None
    assert found.tenant_id == "default"
    assert found.can_authenticate


async def test_a_second_account_on_the_same_address_is_refused(engine) -> None:
    """T21's unique (email). Reported as False, not raised.

    A driver exception here is indistinguishable to the caller from the
    database being unreachable, and the two deserve different handling.
    """
    users, _ = await seeded(engine)

    assert await users.create(user("dup@example.com"))
    # A different id, the same address: the unique index has to be what
    # refuses it, not the primary key.
    assert not await users.create(user("dup@example.com", user_id="a-different-id"))


async def test_the_database_refuses_an_unfolded_address(engine) -> None:
    """The check constraint behind `fold_email`.

    Folding happens in Python, so uniqueness rests on every future caller
    remembering to do it. The constraint is what makes forgetting fail loudly
    instead of creating a second account for one person.
    """
    users, _ = await seeded(engine)

    with pytest.raises(Exception, match="ck_users_email_folded"):
        await users.create(replace(user("mixed@example.com"), email="Mixed@Example.com"))


async def test_the_tenant_foreign_key_is_real(engine) -> None:
    users, _ = await seeded(engine)

    with pytest.raises(Exception, match="fk_users_tenant"):
        await users.create(replace(user("orphan@example.com"), tenant_id="no-such-tenant"))


@pytest.mark.parametrize("role", ["root", "admin", ""])
async def test_a_role_outside_t21s_set_is_refused(engine, role: str) -> None:
    users, _ = await seeded(engine)

    with pytest.raises(Exception, match="ck_users_role"):
        await users.create(replace(user(f"role-{role or 'blank'}@example.com"), role=role))


async def test_rehashing_replaces_the_stored_credential(engine) -> None:
    """The one mutation T21 needs: an Argon2 cost increase applied on login."""
    users, _ = await seeded(engine)

    row = user("rehash@example.com")

    await users.create(row)

    assert await users.set_password_hash(row.user_id, "$argon2id$new$hash")

    found = await users.get(row.user_id)

    assert found is not None
    assert found.password_hash == "$argon2id$new$hash"

    assert not await users.set_password_hash("no-such-user", "x")


async def test_the_service_creates_its_tenant_and_authenticates(engine) -> None:
    """The whole path the CLI drives, against the real schema.

    Argon2 runs here rather than against a fixture hash, which is the only
    place the stored hash column is proven wide enough for a real one.
    """
    sessions = build_session_factory(engine)

    service = AccountService(PgUserRepository(sessions), PgTenantRepository(sessions))

    created = await service.create("Live@Example.COM", GOOD, now=NOW)

    assert created is not None
    assert created.email == "live@example.com"

    assert await service.authenticate("LIVE@example.com", GOOD) is not None
    assert await service.authenticate("live@example.com", "wrong-password-here") is None


async def test_the_unused_t21_columns_are_present_and_null(engine) -> None:
    """Carried to match DDD T21, written by nothing.

    Pinned so that "the column exists" cannot later be mistaken for "the
    feature works" -- a 2FA check reading `totp_enabled` would pass for every
    account, because nothing sets it.
    """
    users, _ = await seeded(engine)

    row = user("unused@example.com")

    await users.create(row)

    async with engine.begin() as conn:
        found = (
            await conn.execute(
                text(
                    "select totp_secret_enc, totp_enabled, email_verified_at "
                    "from identity.users where user_id = :uid"
                ),
                {"uid": row.user_id},
            )
        ).one()

    assert found.totp_secret_enc is None
    assert found.totp_enabled is False
    assert found.email_verified_at is None
