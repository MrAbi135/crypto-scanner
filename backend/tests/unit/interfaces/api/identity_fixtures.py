"""Shared identity collaborators for the API tests.

Every read row is authenticated as of S10-minimal, so each API test needs a
signed token. Built here once rather than in each module: a per-module helper
is how one of them ends up with a subtly different auth setup that passes for
the wrong reason.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime

from scanner.application.identity import AccountService, SessionService
from scanner.application.identity.tokens import AccessTokens
from scanner.application.ports.identity import TenantRecord, UserRecord

# Long enough for `AccessTokens` to accept it. A short one raises, which is the
# point of that check.
TEST_SECRET = "test-signing-secret-not-for-any-real-deployment"

TEST_USER = UserRecord(
    user_id="test-user",
    tenant_id="default",
    email="ops@example.com",
    password_hash="$argon2id$placeholder-never-verified-in-these-tests",
    role="user",
    status="ACTIVE",
    created_at=datetime(2026, 8, 24, tzinfo=UTC),
)


class FakeUsers:
    def __init__(self, rows: list[UserRecord] | None = None) -> None:
        self.rows = {r.user_id: r for r in (rows if rows is not None else [TEST_USER])}

    async def create(self, user: UserRecord) -> bool:
        if user.user_id in self.rows:
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
        self.rows = {
            "default": TenantRecord(
                tenant_id="default",
                name="default",
                status="ACTIVE",
                created_at=datetime(2026, 8, 24, tzinfo=UTC),
            )
        }

    async def upsert(self, tenant: TenantRecord) -> bool:
        if tenant.tenant_id in self.rows:
            return False

        self.rows[tenant.tenant_id] = tenant

        return True

    async def get(self, tenant_id: str) -> TenantRecord | None:
        return self.rows.get(tenant_id)


class FakeSessionStore:
    """T22 in memory, with the same compare-and-set the SQL performs."""

    def __init__(self) -> None:
        self.rows: dict = {}

    async def create(self, session) -> bool:
        if session.session_id in self.rows:
            return False

        if any(r.refresh_hash == session.refresh_hash for r in self.rows.values()):
            return False

        self.rows[session.session_id] = session

        return True

    async def get(self, session_id):
        return self.rows.get(session_id)

    async def rotate(self, session_id, *, expected_hash, new_hash, rotated_at) -> bool:
        row = self.rows.get(session_id)

        if row is None or row.revoked or row.refresh_hash != expected_hash:
            return False

        self.rows[session_id] = replace(
            row,
            refresh_hash=new_hash,
            rotated_at=rotated_at,
            rotation_count=row.rotation_count + 1,
        )

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

    async def list_live_for_user(self, user_id, *, now):
        return tuple(r for r in self.rows.values() if r.user_id == user_id and r.live_at(now))


class EmptySignals:
    """T17/T18/T19 with nothing in them.

    The modules that only exercise the market and coins rows still have to
    supply these, because `build_read_api` requires every collaborator — a
    default would let a test build an app whose signal rows silently 404 for
    the wrong reason.
    """

    async def get(self, signal_id):
        return None

    async def append(self, record):
        return True

    async def latest_for_dedup_key(self, dedup_key):
        return None

    async def recent(self, *, limit, symbol=None, timeframe=None):
        return ()

    async def scan(self, *, batch=500):
        return []

    async def current_state(self, signal_id):
        return None

    async def list_for_signal(self, signal_id):
        return ()

    async def list_live(self, symbol, timeframe):
        return ()

    async def history(self, filters, *, limit, after=None):
        from scanner.application.ports.track_record import HistoryPage

        return HistoryPage(rows=(), next_position=None)

    async def outcome_counts(self, *, group_by, since=None, until=None):
        return ()

    async def list_recent_sweeps(self, *, limit):
        return ()


class EmptyRankings:
    """§9.2 over nothing: a quiet close, which is a real answer."""

    async def snapshot(self, symbols, timeframe, at, *, elapsed_candles: int = 0):
        from scanner.application.ranking import RankingSnapshot

        return RankingSnapshot(
            timeframe=timeframe,
            at=at,
            rows=(),
            gate_passers=0,
            below_floor=0,
        )


class EmptyFeed:
    """§18.4 over nothing. A quiet board is a real answer and not an error."""

    async def read(self):
        from datetime import UTC, datetime

        from scanner.application.feed import Feed

        return Feed(
            generated_at=datetime(2026, 8, 26, 12, tzinfo=UTC),
            rows=(),
            live_total=0,
        )


class EmptyIncidents:
    """A clean ledger. DDD T8 having nothing to report is the good case."""

    async def list_ledger(self, *, symbol=None, open_only=False, limit=100):
        return []


class EmptySymbols:
    """A registry with nothing in it -- distinct from a registry that failed."""

    async def list_universe(self, *, status=None, tier=None, limit=200):
        return []

    async def count_observations(self):
        return {}


def identity(users: FakeUsers | None = None) -> dict:
    """Every collaborator `build_read_api` requires, as kwargs."""

    store = FakeSessionStore()
    people = users or FakeUsers()

    return {
        "accounts": AccountService(people, FakeTenants()),
        "sessions": SessionService(store),
        "session_repository": store,
        "access_tokens": AccessTokens(TEST_SECRET),
        "signals": EmptySignals(),
        "signal_transitions": EmptySignals(),
        "outcomes": EmptySignals(),
        "track_record": EmptySignals(),
        "track_statistics": EmptySignals(),
        "rankings": EmptyRankings(),
        "feed": EmptyFeed(),
        "incidents": EmptyIncidents(),
        "symbols": EmptySymbols(),
    }


def bearer(
    *,
    now: datetime,
    user: UserRecord = TEST_USER,
    session_id: str = "test-session",
) -> dict[str, str]:
    """An `Authorization` header for a valid access token.

    Minted directly rather than by driving `/auth/login`: these tests are about
    the read rows, and a login round trip in each one would make every read
    test also a test of Argon2.
    """
    token = AccessTokens(TEST_SECRET).mint(
        user_id=user.user_id,
        tenant_id=user.tenant_id,
        session_id=session_id,
        role=user.role,
        now=now,
    )

    return {"Authorization": f"Bearer {token}"}
