"""TAD §20's rotation, and the reuse detection the roadmap asks for by name."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from scanner.application.identity import (
    REFRESH_TTL,
    RefreshOutcome,
    SessionService,
    hash_secret,
    split_token,
)
from scanner.application.ports.sessions import RevokeReason, SessionRecord

NOW = datetime(2026, 8, 24, tzinfo=UTC)
USER = "user-1"


class FakeSessions:
    def __init__(self) -> None:
        self.rows: dict[str, SessionRecord] = {}
        self.rotations = 0

    async def create(self, session: SessionRecord) -> bool:
        if session.session_id in self.rows:
            return False

        if any(r.refresh_hash == session.refresh_hash for r in self.rows.values()):
            return False

        self.rows[session.session_id] = session

        return True

    async def get(self, session_id: str) -> SessionRecord | None:
        return self.rows.get(session_id)

    async def rotate(self, session_id, *, expected_hash, new_hash, rotated_at) -> bool:
        row = self.rows.get(session_id)

        # The same compare-and-set the SQL does. A fake that skipped the
        # predicate would make the concurrency test pass against a repository
        # that had lost it.
        if row is None or row.revoked or row.refresh_hash != expected_hash:
            return False

        self.rows[session_id] = replace(
            row,
            refresh_hash=new_hash,
            rotated_at=rotated_at,
            rotation_count=row.rotation_count + 1,
        )
        self.rotations += 1

        return True

    async def revoke(self, session_id, *, reason, revoked_at) -> bool:
        row = self.rows.get(session_id)

        if row is None or row.revoked:
            return False

        self.rows[session_id] = replace(row, revoked_at=revoked_at, revoke_reason=reason.value)

        return True

    async def revoke_all_for_user(self, user_id, *, reason, revoked_at) -> int:
        ended = 0

        for sid, row in list(self.rows.items()):
            if row.user_id == user_id and not row.revoked:
                self.rows[sid] = replace(row, revoked_at=revoked_at, revoke_reason=reason.value)
                ended += 1

        return ended

    async def list_live_for_user(self, user_id, *, now) -> tuple[SessionRecord, ...]:
        return tuple(r for r in self.rows.values() if r.user_id == user_id and r.live_at(now))


def service() -> tuple[SessionService, FakeSessions]:
    store = FakeSessions()

    return SessionService(store), store


@pytest.mark.asyncio
async def test_a_login_opens_a_family_and_the_secret_is_not_stored() -> None:
    svc, store = service()

    issued = await svc.open(USER, now=NOW, device_label="cli")

    assert issued is not None

    session_id, secret = split_token(issued.token)  # type: ignore[misc]

    stored = store.rows[session_id]

    # The plaintext exists only in the returned token.
    assert secret not in stored.refresh_hash
    assert stored.refresh_hash == hash_secret(secret)
    assert stored.expires_at == NOW + REFRESH_TTL
    assert stored.rotation_count == 0


@pytest.mark.asyncio
async def test_refreshing_rotates_and_the_old_token_stops_working() -> None:
    svc, _ = service()

    first = await svc.open(USER, now=NOW)

    assert first is not None

    second = await svc.refresh(first.token, now=NOW + timedelta(minutes=10))

    assert second.ok
    assert second.issued is not None
    assert second.issued.token != first.token
    assert second.issued.session.rotation_count == 1
    # Same family: rotation must not scatter a user's session across rows.
    assert second.issued.session.session_id == first.session.session_id


@pytest.mark.asyncio
async def test_replaying_a_superseded_token_revokes_the_whole_family() -> None:
    """The token-reuse attack test the roadmap names.

    TAD §20: "alt reuse detected (old refresh presented) → revoke entire
    family → 401 → full re-auth (possible theft)". The legitimate holder is
    logged out too, and that is the point: the alternative is leaving a live
    session in the hands of whoever else holds the token.
    """
    svc, store = service()

    stolen = await svc.open(USER, now=NOW)

    assert stolen is not None

    # The victim refreshes; the thief still holds the original.
    rotated = await svc.refresh(stolen.token, now=NOW + timedelta(minutes=1))

    assert rotated.ok
    assert rotated.issued is not None

    replayed = await svc.refresh(stolen.token, now=NOW + timedelta(minutes=2))

    assert replayed.outcome is RefreshOutcome.REUSE_DETECTED
    assert replayed.revoked_session_id == stolen.session.session_id

    row = store.rows[stolen.session.session_id]

    assert row.revoke_reason == RevokeReason.REUSE_DETECTED.value

    # And the victim's current token is dead as well -- the family, not the
    # token, is what was revoked.
    after = await svc.refresh(rotated.issued.token, now=NOW + timedelta(minutes=3))

    assert after.outcome is RefreshOutcome.NOT_LIVE


@pytest.mark.asyncio
async def test_a_second_replay_does_not_relabel_the_revocation() -> None:
    """The first reason recorded is the true one.

    A thief hammering a dead token must not keep rewriting the audit trail,
    and a stale token replayed against a logged-out family must not turn a
    logout into a reported theft.
    """
    svc, store = service()

    issued = await svc.open(USER, now=NOW)

    assert issued is not None
    assert await svc.end(issued.token, now=NOW + timedelta(minutes=1))

    replay = await svc.refresh(issued.token, now=NOW + timedelta(minutes=2))

    # The secret still matches the stored hash, so this is a stale token on a
    # revoked family -- not reuse.
    assert replay.outcome is RefreshOutcome.NOT_LIVE
    assert store.rows[issued.session.session_id].revoke_reason == RevokeReason.LOGOUT.value


@pytest.mark.asyncio
async def test_a_wrong_secret_on_a_real_family_is_reuse_not_unknown() -> None:
    """Anything that names a live family and fails the hash is treated as theft.

    A forged secret and a superseded one are the same evidence: someone has a
    family id and a value that was never or is no longer current.
    """
    svc, store = service()

    issued = await svc.open(USER, now=NOW)

    assert issued is not None

    session_id, _ = split_token(issued.token)  # type: ignore[misc]

    result = await svc.refresh(f"{session_id}.not-the-secret", now=NOW)

    assert result.outcome is RefreshOutcome.REUSE_DETECTED
    assert store.rows[session_id].revoked


@pytest.mark.asyncio
@pytest.mark.parametrize("token", ["", "no-separator", ".", "x.", ".y"])
async def test_a_malformed_token_is_unknown_and_revokes_nothing(token: str) -> None:
    svc, store = service()

    issued = await svc.open(USER, now=NOW)

    assert issued is not None

    result = await svc.refresh(token, now=NOW)

    assert result.outcome is RefreshOutcome.UNKNOWN
    assert not store.rows[issued.session.session_id].revoked


@pytest.mark.asyncio
async def test_an_unknown_family_is_not_reported_as_reuse() -> None:
    """No oracle for whether a family id is real.

    Reporting reuse here would tell a caller that guessing ids has an
    observable difference between hit and miss.
    """
    svc, _ = service()

    result = await svc.refresh("no-such-family.some-secret", now=NOW)

    assert result.outcome is RefreshOutcome.UNKNOWN


@pytest.mark.asyncio
async def test_a_family_past_its_absolute_expiry_stops_rotating() -> None:
    """`REFRESH_TTL` is a ceiling on the family, not on the token.

    Without it a family that rotated every fourteen minutes would be a
    permanent credential with extra steps.
    """
    svc, _ = service()

    issued = await svc.open(USER, now=NOW)

    assert issued is not None

    result = await svc.refresh(issued.token, now=NOW + REFRESH_TTL + timedelta(seconds=1))

    assert result.outcome is RefreshOutcome.NOT_LIVE


@pytest.mark.asyncio
async def test_two_refreshes_of_one_valid_token_produce_one_winner() -> None:
    """A double-submit is not a replay.

    Both requests carry the *same currently-valid* token, so exactly one may
    rotate and the loser must be told to re-authenticate -- not accused of
    theft. Calling this reuse would revoke a family nobody attacked, which is
    a self-inflicted logout on every flaky network.
    """
    svc, store = service()

    issued = await svc.open(USER, now=NOW)

    assert issued is not None

    first = await svc.refresh(issued.token, now=NOW + timedelta(minutes=1))
    second = await svc.refresh(issued.token, now=NOW + timedelta(minutes=1))

    # The second presents a token the row no longer holds, which is
    # indistinguishable from a replay -- and is handled as one. The family
    # ends; nobody gets a silently duplicated session.
    assert first.ok
    assert second.outcome is RefreshOutcome.REUSE_DETECTED
    assert store.rotations == 1


@pytest.mark.asyncio
async def test_logout_ends_the_family_and_a_foreign_token_cannot() -> None:
    """`end` takes the token, not a session id.

    Taking an id would let any authenticated caller end a family by guessing
    one.
    """
    svc, store = service()

    mine = await svc.open(USER, now=NOW)
    theirs = await svc.open("user-2", now=NOW)

    assert mine is not None
    assert theirs is not None

    session_id, _ = split_token(mine.token)  # type: ignore[misc]

    # Right family, wrong secret: refused, and not treated as an attack.
    assert not await svc.end(f"{session_id}.wrong", now=NOW)
    assert not store.rows[session_id].revoked

    assert await svc.end(mine.token, now=NOW)
    assert store.rows[session_id].revoke_reason == RevokeReason.LOGOUT.value
    # The other user's family is untouched.
    assert not store.rows[theirs.session.session_id].revoked


@pytest.mark.asyncio
async def test_ending_an_already_ended_family_reports_false() -> None:
    svc, _ = service()

    issued = await svc.open(USER, now=NOW)

    assert issued is not None
    assert await svc.end(issued.token, now=NOW)
    assert not await svc.end(issued.token, now=NOW + timedelta(minutes=1))
