"""Application ports for T20 `identity.tenants` and T21 `identity.users`."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

# T21 names these; nothing in S10-minimal writes or reads them for a decision.
# Listed so that "the column exists" never gets mistaken for "the feature
# works" -- the failure mode that `totp_enabled` defaulting to false invites,
# where a 2FA check reads a column nothing sets and passes every time.
UNUSED_IDENTITY_COLUMNS: tuple[str, ...] = (
    "totp_secret_enc",
    "totp_enabled",
    "email_verified_at",
)


@dataclass(frozen=True, slots=True)
class TenantRecord:
    tenant_id: str
    name: str
    status: str
    created_at: datetime


@dataclass(frozen=True, slots=True)
class UserRecord:
    """One account. `password_hash` never leaves the identity layer."""

    user_id: str
    tenant_id: str
    email: str
    password_hash: str
    role: str
    status: str
    created_at: datetime
    deleted_at: datetime | None = None

    @property
    def can_authenticate(self) -> bool:
        """§18.1's login gate, in one place.

        A deleted or non-ACTIVE account must fail the same way a wrong
        password does. Spread across call sites this is the check someone
        adds to `/auth/login` and forgets on `/auth/refresh`, leaving a
        disabled account alive for as long as it keeps rotating.
        """
        return self.status == "ACTIVE" and self.deleted_at is None


class TenantRepository(Protocol):
    async def upsert(self, tenant: TenantRecord) -> bool: ...

    async def get(self, tenant_id: str) -> TenantRecord | None: ...


class UserRepository(Protocol):
    async def create(self, user: UserRecord) -> bool:
        """Insert one account. False when the email is already taken."""
        ...

    async def get_by_email(self, email: str) -> UserRecord | None:
        """Look up by case-folded email. The caller folds; see migration 019."""
        ...

    async def get(self, user_id: str) -> UserRecord | None: ...

    async def list_all(self) -> tuple[UserRecord, ...]:
        """Every account, for the operator CLI. Not an API surface."""
        ...

    async def set_password_hash(self, user_id: str, password_hash: str) -> bool:
        """Replace the stored hash.

        The one mutation this table needs in S10-minimal: Argon2 encodes its
        cost parameters into the hash, so raising them re-hashes on next
        successful login rather than locking anyone out.
        """
        ...
