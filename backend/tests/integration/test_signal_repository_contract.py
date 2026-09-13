"""The golden harness's T17 double, held to the same contract as Postgres.

`InMemorySignalRepository` is what lets a golden dataset show a signal being
published at all: without a signal repository the confluence engine returns
from `_publish` before §15.3 is evaluated. That makes the double load-bearing.
If it drifted from the real table -- a dedup lookup that picked a different
row, a second append that overwrote instead of refusing, a scan in another
order -- a golden could pass while the engine's real publish path behaves
differently, and nothing would say so.

So every test here runs twice, once against each implementation, and asserts
the same observable answer. The questions are the ones the publish path and
its readers actually ask of the port: insert-once, the newest signal on a
dedup key, the tie-break on one close, and the two listing orders.

**Deliberately outside the contract:** the table's CHECK constraints (grade
band, entry-band width) and the append-only triggers. The double does not
enforce them, and a copy of those rules in the harness would be a second
definition of what the migrations already own. `test_publish_path_pg.py` runs
the golden's own signal through the real table as the application role, which
is where they are exercised on a real publication.

Requires Docker (testcontainers) for the Postgres half.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from itertools import count

import pytest

pytest.importorskip("testcontainers")

from scanner.application.ports.signals import SignalRecord
from scanner.infrastructure.persistence.database import build_engine, build_session_factory
from scanner.infrastructure.persistence.signal_repository import PgSignalRepository
from scanner.shared import Timeframe
from tests.golden.harness.memory import InMemorySignalRepository

pytestmark = pytest.mark.integration

T0 = datetime(2026, 4, 6, 8, tzinfo=UTC)

# The container is shared across the whole integration session and T17 cannot
# be cleaned (it is append-only by design), so every test writes under its own
# symbol and ids rather than assuming an empty table.
_namespaces = count()


@pytest.fixture(params=["memory", "postgres"])
async def repo(request, pg_dsn):
    if request.param == "memory":
        yield InMemorySignalRepository()
        return

    engine = build_engine(pg_dsn, pool_size=2)

    yield PgSignalRepository(build_session_factory(engine))

    await engine.dispose()


@pytest.fixture()
def ns() -> str:
    return f"CONTRACT{next(_namespaces)}"


def signal(
    ns: str,
    suffix: str,
    *,
    published_at: datetime = T0,
    dedup: str = "key",
    timeframe: Timeframe = Timeframe.H1,
) -> SignalRecord:
    # Field values the real table accepts (the same ones its own tests use),
    # so a Postgres refusal here would be a contract failure and not a fixture
    # that broke a CHECK constraint.
    return SignalRecord(
        signal_id=f"{ns}-{suffix}",
        setup_id=f"{ns}-setup-{suffix}",
        symbol=ns,
        timeframe=timeframe,
        direction="UP",
        archetype="A3",
        grade="B",
        final_confidence=Decimal(70),
        entry_proximal=Decimal("120.45"),
        entry_distal=Decimal("115.5"),
        invalidation_level=Decimal("115.5"),
        target_bands='[{"low":"129.5","high":"129.5"}]',
        published_at=published_at,
        ttl_candles=18,
        algo_version="s8-test",
        param_set_version="2026.08.24.2",
        payload='{"symbol":"CONTRACT"}',
        payload_hash="c" * 64,
        dedup_key=f"{ns}|{dedup}",
    )


async def test_a_second_append_on_one_id_is_refused_and_the_first_write_stands(repo, ns) -> None:
    """Insert-once. An upsert-shaped double would pass a golden that published
    twice on one id and quietly keep the second payload."""

    first = signal(ns, "a")

    assert await repo.append(first) is True
    assert await repo.append(replace(first, payload_hash="d" * 64)) is False

    # Equality on the whole record. Decimal compares by value, so the numeric
    # column's padded scale on the way back does not count as a difference.
    assert await repo.get(first.signal_id) == first


async def test_get_answers_none_for_an_id_never_written(repo, ns) -> None:
    assert await repo.get(f"{ns}-missing") is None


async def test_the_dedup_lookup_returns_the_newest_publication(repo, ns) -> None:
    older = signal(ns, "old", published_at=T0)
    newer = signal(ns, "new", published_at=T0 + timedelta(hours=5))

    # Written newest-first, so an implementation that answered "last
    # inserted" instead of "latest published" gets the wrong row.
    await repo.append(newer)
    await repo.append(older)

    assert await repo.latest_for_dedup_key(newer.dedup_key) == newer
    assert await repo.latest_for_dedup_key(f"{ns}|no-such-key") is None


async def test_a_dedup_tie_on_one_close_is_broken_by_the_signal_id(repo, ns) -> None:
    """Two publications on the same close: Postgres orders `signal_id DESC`
    after `published_at DESC`, so the double must pick the same one."""

    low = signal(ns, "a", published_at=T0)
    high = signal(ns, "b", published_at=T0)

    await repo.append(high)
    await repo.append(low)

    assert await repo.latest_for_dedup_key(low.dedup_key) == high


async def test_one_key_may_be_published_again_later(repo, ns) -> None:
    """The dedup index is not unique: §10.3 merges only into a *live* signal,
    and whether the older one still is belongs to the caller's TTL arithmetic."""

    first = signal(ns, "first", published_at=T0)
    later = signal(ns, "later", published_at=T0 + timedelta(days=30))

    assert await repo.append(first) is True
    assert await repo.append(later) is True
    assert await repo.latest_for_dedup_key(first.dedup_key) == later


async def test_recent_is_newest_first_filtered_and_limited(repo, ns) -> None:
    rows = [
        signal(ns, "t0", published_at=T0),
        signal(ns, "t1a", published_at=T0 + timedelta(hours=1)),
        signal(ns, "t1b", published_at=T0 + timedelta(hours=1)),
        signal(ns, "h4", published_at=T0 + timedelta(hours=2), timeframe=Timeframe.H4),
    ]

    for row in rows:
        await repo.append(row)

    assert await repo.recent(limit=3, symbol=ns) == (rows[3], rows[2], rows[1])
    assert await repo.recent(limit=10, symbol=ns, timeframe=Timeframe.H1) == (
        rows[2],
        rows[1],
        rows[0],
    )


async def test_scan_is_oldest_first_with_the_same_tie_break(repo, ns) -> None:
    rows = [
        signal(ns, "t1b", published_at=T0 + timedelta(hours=1)),
        signal(ns, "t0", published_at=T0),
        signal(ns, "t1a", published_at=T0 + timedelta(hours=1)),
    ]

    for row in rows:
        await repo.append(row)

    # The shared table holds other modules' signals, so only this namespace
    # is compared -- the order within it is the contract.
    mine = [row for row in await repo.scan() if row.symbol == ns]

    assert mine == [rows[1], rows[2], rows[0]]
