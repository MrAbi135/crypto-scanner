"""Application port for T22 `identity.sessions`."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Protocol


class RevokeReason(str, Enum):
    """Why a family ended. Never null beside a `revoked_at` — see migration 020.

    `REUSE_DETECTED` is the one that matters: it is the difference between a
    user signing out and a token being replayed by someone who should not
    have it, and it is the only one a human needs to be told about.
    """

    LOGOUT = "logout"
    REUSE_DETECTED = "reuse_detected"
    USER_REVOKED = "user_revoked"
    # S105 is suppressed below because it matches the member *name*: this
    # is a revocation reason, not a credential. Renaming it to dodge the
    # rule would make the audit trail less clear to satisfy a scanner.
    PASSWORD_CHANGED = "password_changed"  # noqa: S105


@dataclass(frozen=True, slots=True)
class SessionRecord:
    """One refresh-token family.

    `refresh_hash` is the sha256 of the *current* secret only. The superseded
    one is overwritten, which is what makes a replay detectable: a token that
    names this family and does not match is either stale or stolen, and TAD
    §20 treats both the same way.
    """

    session_id: str
    user_id: str
    refresh_hash: str
    issued_at: datetime
    rotated_at: datetime
    expires_at: datetime
    rotation_count: int
    device_label: str | None = None
    ip_created: str | None = None
    revoked_at: datetime | None = None
    revoke_reason: str | None = None

    @property
    def revoked(self) -> bool:
        return self.revoked_at is not None

    def live_at(self, now: datetime) -> bool:
        """Not revoked and not past its absolute expiry.

        Both, and on the record rather than at call sites. A family that only
        checked revocation would rotate forever; one that only checked expiry
        would keep serving a family revoked for theft until its expiry ran
        out.
        """
        return not self.revoked and now < self.expires_at


class SessionRepository(Protocol):
    async def create(self, session: SessionRecord) -> bool:
        """Open a family. False when the id or the refresh hash already exists."""
        ...

    async def get(self, session_id: str) -> SessionRecord | None: ...

    async def rotate(
        self,
        session_id: str,
        *,
        expected_hash: str,
        new_hash: str,
        rotated_at: datetime,
    ) -> bool:
        """Advance the family, only if it still holds `expected_hash`.

        The compare-and-set is the whole point and it belongs in the
        statement, not around it. Two refreshes arriving together with the
        same valid token would both read the row, both find it valid, and both
        write — handing out two live tokens for one family and leaving the
        loser's user to trip the reuse alarm later. As a conditional UPDATE,
        exactly one wins.

        False means the row moved underneath: already rotated, or revoked.
        """
        ...

    async def revoke(
        self,
        session_id: str,
        *,
        reason: RevokeReason,
        revoked_at: datetime,
    ) -> bool:
        """End one family. False when it was already revoked.

        Idempotent by design: the reuse path and a concurrent logout can both
        arrive, and the first reason recorded is the true one.
        """
        ...

    async def revoke_all_for_user(
        self,
        user_id: str,
        *,
        reason: RevokeReason,
        revoked_at: datetime,
    ) -> int:
        """End every live family for a user. Returns how many were ended."""
        ...

    async def list_live_for_user(
        self,
        user_id: str,
        *,
        now: datetime,
    ) -> tuple[SessionRecord, ...]:
        """§18.1's `GET /auth/sessions`: active families, newest activity first."""
        ...
