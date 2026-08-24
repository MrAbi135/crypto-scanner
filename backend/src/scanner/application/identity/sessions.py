"""TAD §20's refresh rotation, and the reuse detection that makes it worth doing.

Rotation on its own buys little: a stolen refresh token still works until it
expires. What makes rotation a defence is that the *legitimate* holder and the
thief cannot both keep using the family. Whoever refreshes second presents a
token the row no longer holds, and that presentation is the alarm:

> alt reuse detected (old refresh presented) → revoke entire family → 401 →
> full re-auth (possible theft)   — TAD §20

The victim is logged out. That is the intended outcome, not a side effect: the
alternative is leaving a live session in the hands of whoever else has the
token.

**The token carries its family id.** `{session_id}.{secret}` — the id locates
the row, the secret is compared against its hash. Without the id, detecting
reuse would mean searching for a hash that is deliberately no longer stored,
and a replayed token would be indistinguishable from a forged one. It is not
a secret: it is a random 128-bit id that identifies a row, and holding it
without the secret gets you a `TOKEN_REVOKED`.

**Comparison is constant-time.** The stored value is a hash, so a timing leak
here reveals hash prefixes rather than the secret — but a hash prefix is
enough to shorten a search, and `hmac.compare_digest` costs nothing.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum

from scanner.application.ports.sessions import (
    RevokeReason,
    SessionRecord,
    SessionRepository,
)

# TAD §20: "refresh token (rotating, httpOnly secure cookie)". Thirty days is
# the absolute ceiling on a family regardless of how often it rotates -- a
# family that could rotate forever is a permanent credential with extra steps.
REFRESH_TTL = timedelta(days=30)

# 256 bits, url-safe. `token_urlsafe(32)` yields 43 characters.
_SECRET_BYTES = 32

# 128 bits is plenty for an identifier that is not itself a secret.
_SESSION_ID_BYTES = 16

_SEPARATOR = "."


class RefreshOutcome(str, Enum):
    """What a presented refresh token turned out to be."""

    ROTATED = "ROTATED"
    # Malformed, or names a family that does not exist. Deliberately not
    # distinguished from the below: telling a caller "that family exists but
    # your secret is wrong" confirms a valid family id.
    UNKNOWN = "UNKNOWN"
    # The family exists and this token is not its current one. TAD §20's
    # theft signal; the family is revoked before this is returned.
    REUSE_DETECTED = "REUSE_DETECTED"
    # Revoked earlier, or past `expires_at`.
    NOT_LIVE = "NOT_LIVE"


@dataclass(frozen=True, slots=True)
class IssuedRefresh:
    """A refresh token, and the family it belongs to.

    `token` is the only place the plaintext exists. It is returned to the
    caller once and never stored — T22 holds its hash.
    """

    token: str
    session: SessionRecord


@dataclass(frozen=True, slots=True)
class RefreshResult:
    outcome: RefreshOutcome
    issued: IssuedRefresh | None = None
    # Set when a family was revoked as part of handling this token, so the
    # caller can audit the theft signal rather than seeing a bare 401.
    revoked_session_id: str | None = None

    @property
    def ok(self) -> bool:
        return self.outcome is RefreshOutcome.ROTATED


def hash_secret(secret: str) -> str:
    """sha256 of the refresh secret. See migration 020 for why not Argon2."""

    return hashlib.sha256(secret.encode("utf-8")).hexdigest()


def _mint() -> tuple[str, str, str]:
    """A fresh (session_id, secret, token) triple."""

    session_id = secrets.token_urlsafe(_SESSION_ID_BYTES)
    secret = secrets.token_urlsafe(_SECRET_BYTES)

    return session_id, secret, f"{session_id}{_SEPARATOR}{secret}"


def split_token(token: str) -> tuple[str, str] | None:
    """`{session_id}.{secret}` → its parts, or None if it is not that shape.

    `rsplit` on one separator: `token_urlsafe` never emits the separator, but
    parsing from the right means a future id format containing one would break
    loudly here rather than silently truncating a secret.
    """
    if _SEPARATOR not in token:
        return None

    session_id, _, secret = token.rpartition(_SEPARATOR)

    if not session_id or not secret:
        return None

    return session_id, secret


@dataclass(frozen=True, slots=True)
class SessionService:
    sessions: SessionRepository

    async def open(
        self,
        user_id: str,
        *,
        now: datetime,
        device_label: str | None = None,
        ip_created: str | None = None,
    ) -> IssuedRefresh | None:
        """Start a family at login. None if the id or hash collided.

        A collision is a 128-bit or 256-bit coincidence and returning None
        rather than retrying is deliberate: at that probability, a collision
        is far more likely to mean the generator is broken, and a retry loop
        would hide it.
        """
        session_id, secret, token = _mint()

        record = SessionRecord(
            session_id=session_id,
            user_id=user_id,
            refresh_hash=hash_secret(secret),
            issued_at=now,
            rotated_at=now,
            expires_at=now + REFRESH_TTL,
            rotation_count=0,
            device_label=device_label,
            ip_created=ip_created,
        )

        if not await self.sessions.create(record):
            return None

        return IssuedRefresh(token=token, session=record)

    async def refresh(self, token: str, *, now: datetime) -> RefreshResult:
        """Rotate a family, or detect that the token should not exist."""

        parts = split_token(token)

        if parts is None:
            return RefreshResult(RefreshOutcome.UNKNOWN)

        session_id, secret = parts

        record = await self.sessions.get(session_id)

        if record is None:
            return RefreshResult(RefreshOutcome.UNKNOWN)

        presented = hash_secret(secret)

        if not hmac.compare_digest(presented, record.refresh_hash):
            # The family is real and this is not its token. Revoke first, then
            # report -- a caller that returned 401 and left the revoke to a
            # later step would leave the thief's token live in between.
            #
            # Revoked even when the family is already dead: `revoke` is
            # idempotent and keeps the first reason, so a replay against a
            # logged-out family does not overwrite `logout` with
            # `reuse_detected`.
            if not record.revoked:
                await self.sessions.revoke(
                    session_id,
                    reason=RevokeReason.REUSE_DETECTED,
                    revoked_at=now,
                )

            return RefreshResult(
                RefreshOutcome.REUSE_DETECTED,
                revoked_session_id=session_id,
            )

        # The secret matched, so this is the legitimate holder -- but the
        # family may still be revoked or expired. Checked after the hash
        # comparison on purpose: answering "not live" to a wrong secret would
        # confirm that the family id names something real.
        if not record.live_at(now):
            return RefreshResult(RefreshOutcome.NOT_LIVE)

        new_secret = secrets.token_urlsafe(_SECRET_BYTES)
        new_hash = hash_secret(new_secret)

        rotated = await self.sessions.rotate(
            session_id,
            expected_hash=record.refresh_hash,
            new_hash=new_hash,
            rotated_at=now,
        )

        if not rotated:
            # Someone else rotated this family between the read and the write.
            # Not treated as reuse: both requests carried the *same valid*
            # token, so this is a double-submit, not a replay of a superseded
            # one. Reporting NOT_LIVE costs the loser one re-login; calling it
            # theft would revoke a family nobody attacked.
            return RefreshResult(RefreshOutcome.NOT_LIVE)

        return RefreshResult(
            RefreshOutcome.ROTATED,
            issued=IssuedRefresh(
                token=f"{session_id}{_SEPARATOR}{new_secret}",
                session=SessionRecord(
                    session_id=record.session_id,
                    user_id=record.user_id,
                    refresh_hash=new_hash,
                    issued_at=record.issued_at,
                    rotated_at=now,
                    expires_at=record.expires_at,
                    rotation_count=record.rotation_count + 1,
                    device_label=record.device_label,
                    ip_created=record.ip_created,
                ),
            ),
        )

    async def end(self, token: str, *, now: datetime) -> bool:
        """§18.1's logout: end the family this token belongs to.

        Takes the token rather than a session id so a caller cannot end
        someone else's family by guessing one. A token that does not match is
        not treated as reuse — a logout is not an attack, and revoking on a
        stale logout would turn a double-click into a security event.
        """
        parts = split_token(token)

        if parts is None:
            return False

        session_id, secret = parts

        record = await self.sessions.get(session_id)

        if record is None or not hmac.compare_digest(hash_secret(secret), record.refresh_hash):
            return False

        return await self.sessions.revoke(
            session_id,
            reason=RevokeReason.LOGOUT,
            revoked_at=now,
        )
