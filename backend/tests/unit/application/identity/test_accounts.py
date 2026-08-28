"""Account creation and the credential check."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime

import pytest

from scanner.application.identity import (
    MIN_PASSWORD_LENGTH,
    AccountService,
    PasswordPolicyError,
    fold_email,
    hash_password,
    user_id_for,
    verify_password,
)
from scanner.application.ports.identity import TenantRecord, UserRecord

NOW = datetime(2026, 8, 24, tzinfo=UTC)
GOOD = "correct-horse-battery-staple"


class FakeUsers:
    def __init__(self) -> None:
        self.rows: dict[str, UserRecord] = {}

    async def create(self, user: UserRecord) -> bool:
        if user.user_id in self.rows or any(r.email == user.email for r in self.rows.values()):
            return False

        self.rows[user.user_id] = user

        return True

    async def get_by_email(self, email: str) -> UserRecord | None:
        return next((r for r in self.rows.values() if r.email == email), None)

    async def get(self, user_id: str) -> UserRecord | None:
        return self.rows.get(user_id)

    async def list_all(self) -> tuple[UserRecord, ...]:
        return tuple(self.rows.values())

    async def set_password_hash(self, user_id: str, password_hash: str) -> bool:
        row = self.rows.get(user_id)

        if row is None:
            return False

        self.rows[user_id] = replace(row, password_hash=password_hash)

        return True


class FakeTenants:
    def __init__(self) -> None:
        self.rows: dict[str, TenantRecord] = {}

    async def upsert(self, tenant: TenantRecord) -> bool:
        if tenant.tenant_id in self.rows:
            return False

        self.rows[tenant.tenant_id] = tenant

        return True

    async def get(self, tenant_id: str) -> TenantRecord | None:
        return self.rows.get(tenant_id)


def service() -> tuple[AccountService, FakeUsers, FakeTenants]:
    users, tenants = FakeUsers(), FakeTenants()

    return AccountService(users, tenants), users, tenants


@pytest.mark.asyncio
async def test_an_account_is_created_and_can_authenticate() -> None:
    svc, _, tenants = service()

    user = await svc.create("Ops@Example.COM", GOOD, now=NOW)

    assert user is not None
    assert user.email == "ops@example.com"
    assert user.role == "user"
    # T20's row exists, so the foreign key is real from the first account
    # rather than backfilled the day tenancy matters.
    assert tenants.rows["default"].status == "ACTIVE"

    assert await svc.authenticate("ops@example.com", GOOD) is not None


@pytest.mark.asyncio
async def test_the_address_is_matched_however_it_is_typed() -> None:
    """T21: "unique, case-folded".

    Folding at the boundary rather than in an index expression: one spelling
    reaches storage, so a caller cannot look up a variant, miss, and create a
    second account for the same person.
    """
    svc, _, _ = service()

    await svc.create("Ops@Example.com", GOOD, now=NOW)

    assert await svc.authenticate("  OPS@EXAMPLE.COM  ", GOOD) is not None
    assert await svc.create("ops@example.com", GOOD, now=NOW) is None


def test_folding_handles_more_than_case() -> None:
    """`casefold` and NFKC, not `lower`.

    `lower` leaves `ß` alone; `casefold` maps it to `ss`, so a German address
    does not get a second account for free. NFKC collapses the ligature a mail
    server already treats as identical.
    """
    assert fold_email("STRASSE@x.de") == fold_email("Straße@x.de")
    assert fold_email("oﬃce@x.com") == "office@x.com"

    # And the id follows the folding, so the two spellings are one row.
    assert user_id_for("Straße@x.de") == user_id_for("strasse@x.de")


@pytest.mark.asyncio
async def test_a_wrong_password_and_an_unknown_address_are_indistinguishable() -> None:
    """§18.1: bad credentials return `AUTH_REQUIRED`, "deliberately same code,
    no user-enumeration"."""
    svc, _, _ = service()

    await svc.create("ops@example.com", GOOD, now=NOW)

    assert await svc.authenticate("ops@example.com", "wrong-password-here") is None
    assert await svc.authenticate("nobody@example.com", GOOD) is None


@pytest.mark.asyncio
async def test_the_unknown_address_path_still_hashes() -> None:
    """The timing half of the same rule.

    Returning early on "no such user" makes a miss microseconds and a hit the
    ~50 ms Argon2 costs, which answers "is this address registered?" without
    the response body saying so. Asserted by measurement rather than by
    reading the code, because the early return is exactly the change someone
    makes later for performance.
    """
    import time

    svc, _, _ = service()

    await svc.create("ops@example.com", GOOD, now=NOW)

    start = time.perf_counter()
    await svc.authenticate("ops@example.com", "wrong-password-here")
    known = time.perf_counter() - start

    start = time.perf_counter()
    await svc.authenticate("nobody@example.com", GOOD)
    unknown = time.perf_counter() - start

    # Generous: this asserts the same order of magnitude, not a constant time.
    # A skipped hash is ~1000x faster, which this catches; scheduler noise on
    # a loaded CI box is not.
    assert unknown > known / 10


@pytest.mark.asyncio
async def test_a_disabled_account_cannot_authenticate_even_with_the_right_password() -> None:
    """`can_authenticate` lives on the record so every path asks the same
    question. Spread across call sites, this is the check that gets added to
    login and forgotten on refresh."""
    svc, users, _ = service()

    user = await svc.create("ops@example.com", GOOD, now=NOW)

    assert user is not None

    users.rows[user.user_id] = replace(user, status="DISABLED")

    assert await svc.authenticate("ops@example.com", GOOD) is None


@pytest.mark.asyncio
async def test_a_deleted_account_cannot_authenticate() -> None:
    svc, users, _ = service()

    user = await svc.create("ops@example.com", GOOD, now=NOW)

    assert user is not None

    users.rows[user.user_id] = replace(user, deleted_at=NOW)

    assert await svc.authenticate("ops@example.com", GOOD) is None


@pytest.mark.asyncio
async def test_a_short_password_is_refused_on_creation_not_on_login() -> None:
    svc, _, _ = service()

    with pytest.raises(PasswordPolicyError, match=str(MIN_PASSWORD_LENGTH)):
        await svc.create("ops@example.com", "short", now=NOW)


def test_a_corrupt_hash_verifies_false_rather_than_raising() -> None:
    """A caller that could tell "wrong password" from "corrupt hash" apart is
    one refactor from telling the client which."""
    assert not verify_password(GOOD, "not-an-argon2-hash")
    assert not verify_password(GOOD, "")


def test_two_hashes_of_one_password_differ() -> None:
    """Salted, which is the whole point. Equal hashes would mean a stolen
    table is a rainbow-table lookup."""
    assert hash_password(GOOD) != hash_password(GOOD)
    assert verify_password(GOOD, hash_password(GOOD))


@pytest.mark.asyncio
async def test_set_password_replaces_the_credential() -> None:
    """`create` refuses an address that already exists, and rightly -- a
    provisioning re-run must not silently overwrite a live credential. This is
    the deliberate version, which is why it is a separate verb."""

    svc, _, _ = service()

    await svc.create("ops@example.com", GOOD, now=NOW)

    changed = await svc.set_password("ops@example.com", "a-different-passphrase")

    assert changed is not None
    assert await svc.authenticate("ops@example.com", "a-different-passphrase") is not None
    # The old one stops working, which is the entire point.
    assert await svc.authenticate("ops@example.com", GOOD) is None


@pytest.mark.asyncio
async def test_set_password_folds_the_address_like_every_other_lookup() -> None:
    """Otherwise the operator who created `Ops@Example.COM` cannot reset it
    without remembering the capitalisation they used."""

    svc, _, _ = service()

    await svc.create("Ops@Example.COM", GOOD, now=NOW)

    assert await svc.set_password("OPS@example.com", "a-different-passphrase") is not None


@pytest.mark.asyncio
async def test_set_password_refuses_an_address_with_no_account() -> None:
    """Distinct from `create`'s "already exists" for the mirror reason: this
    one needs an account and there is none. Creating it here would turn a
    typo'd address into a second account nobody asked for."""

    svc, users, _ = service()

    assert await svc.set_password("nobody@example.com", GOOD) is None
    assert users.rows == {}


@pytest.mark.asyncio
async def test_set_password_applies_the_same_policy_as_create() -> None:
    """A password set here has to be one `authenticate` will take, so it goes
    through the same gate. Refused *before* anything is written."""

    svc, _, _ = service()

    await svc.create("ops@example.com", GOOD, now=NOW)

    with pytest.raises(PasswordPolicyError):
        await svc.set_password("ops@example.com", "x" * (MIN_PASSWORD_LENGTH - 1))

    # The old credential survives a refusal; a half-applied reset would lock
    # the account out with no way back in.
    assert await svc.authenticate("ops@example.com", GOOD) is not None
