"""The interim notifier's role, attacked: it can read three things and nothing else.

**Deleted at S18 with the notifier it guards.**

`ops/notify/notify_signals.py` promises never to push a candidate that skipped
the §15.3 publication gates. A path whitelist inside that file states the
promise; this role is what keeps it. `detection.setups` -- the table behind
`GET /api/v1/rankings`, below-floor rows included by its own design -- must
answer "permission denied", so that widening the notifier by accident is not a
code review question but a runtime impossibility.

Four things have to be true, and the fourth is the one a careless version gets
wrong:

1. the two signal tables are readable, and the tier columns §10.1 needs;
2. everything else is refused -- other schemas, other tables in the same
   schema, and other *columns* of the same table;
3. no write of any kind succeeds, and the role cannot grant itself more;
4. nothing the notifier legitimately does is refused. A grant that also blocked
   the `market.symbols` join would pass every attack here and then fail closed
   in production, which reads as "the notifier is broken" rather than "the test
   was wrong".

Requires Docker (testcontainers). Run: pytest -m integration tests/integration
"""

from __future__ import annotations

import pytest

pytest.importorskip("testcontainers")

from sqlalchemy import text
from sqlalchemy.exc import ProgrammingError

from tests.support.notify_role import NOTIFY_ROLE_SQL, notify_role_statements

pytestmark = pytest.mark.integration


# Everything the notifier is allowed to read, and the statement that proves it.
_PERMITTED = (
    ("detection.signals", "select count(*) from detection.signals"),
    ("detection.signal_transitions", "select count(*) from detection.signal_transitions"),
    (
        "market.symbols tier columns",
        "select exchange_symbol, tier, status from market.symbols",
    ),
    # `assert_tier_readable()` in the notifier runs exactly this, and a
    # column-level grant is not obviously enough for it -- `count(*)` names no
    # column. If postgres ever decides it is not, the notifier fails closed on
    # every run and this is the test that says why.
    ("market.symbols count(*)", "select count(*) from market.symbols"),
)

# Reads that must be refused. The first is the whole point of the exercise.
_FORBIDDEN_READS = (
    ("detection.setups", "select count(*) from detection.setups"),
    ("detection.engine_events", "select count(*) from detection.engine_events"),
    ("detection.signal_outcomes", "select count(*) from detection.signal_outcomes"),
    ("identity.users", "select count(*) from identity.users"),
    ("market.candles", "select count(*) from market.candles"),
    # Same table, ungranted columns. A table-level grant would pass every other
    # case in this tuple and fail these two.
    ("market.symbols.*", "select * from market.symbols"),
    ("market.symbols.wash_risk", "select wash_risk from market.symbols"),
    ("market.symbols.exclusion_reason", "select exclusion_reason from market.symbols"),
)

_FORBIDDEN_WRITES = (
    ("insert signal", "insert into detection.signals (signal_id) values ('probe')"),
    ("update signal", "update detection.signals set grade = 'X' where false"),
    ("delete signal", "delete from detection.signals where false"),
    (
        "insert transition",
        "insert into detection.signal_transitions (transition_id) values ('probe')",
    ),
    ("update symbol tier", "update market.symbols set tier = 'T1' where false"),
    ("create table", "create table detection.probe (x int)"),
)


async def test_the_operator_sql_is_the_file_this_test_reads() -> None:
    """A guard against the two copies drifting: there is exactly one."""

    assert NOTIFY_ROLE_SQL.is_file(), f"missing {NOTIFY_ROLE_SQL}"

    statements = notify_role_statements()

    assert any("GRANT SELECT (exchange_symbol, tier, status)" in s for s in statements), (
        "the tier grant must stay column-scoped; a table-level grant would hand "
        "the notifier the liquidity figures and the delisting record too"
    )
    assert not any("ALTER DEFAULT PRIVILEGES" in s for s in statements), (
        "default privileges would silently grant the notifier every table a future migration adds"
    )


@pytest.mark.parametrize(("what", "sql"), _PERMITTED, ids=[w for w, _ in _PERMITTED])
async def test_the_notifier_role_can_read_what_it_needs(
    notify_engine_,
    what: str,
    sql: str,
) -> None:
    """Criterion 4. Without this the suite would pass on a role that reads nothing."""

    async with notify_engine_.connect() as conn:
        await conn.execute(text(sql))


@pytest.mark.parametrize(("what", "sql"), _FORBIDDEN_READS, ids=[w for w, _ in _FORBIDDEN_READS])
async def test_the_notifier_role_cannot_read_anything_else(
    notify_engine_,
    what: str,
    sql: str,
) -> None:
    """Criterion 2, and the message matters.

    Asserting only "an error happened" would pass if the table had been
    renamed out from under the test. A permission denial names the layer that
    refused.
    """
    async with notify_engine_.connect() as conn:
        with pytest.raises(ProgrammingError) as refusal:
            await conn.execute(text(sql))

    assert "permission denied" in str(refusal.value).lower(), (
        f"{what} was refused, but not by a grant: {refusal.value}"
    )


@pytest.mark.parametrize(("what", "sql"), _FORBIDDEN_WRITES, ids=[w for w, _ in _FORBIDDEN_WRITES])
async def test_the_notifier_role_cannot_write_anything(
    notify_engine_,
    what: str,
    sql: str,
) -> None:
    """Criterion 3.

    Refused by the grant, before the migration-018 append-only triggers get a
    chance. `append_only_violation` here would mean the role holds UPDATE and
    is only being stopped by a trigger it could ask the owner to disable.
    """
    async with notify_engine_.connect() as conn:
        with pytest.raises(ProgrammingError) as refusal:
            await conn.execute(text(sql))

    message = str(refusal.value).lower()

    assert "permission denied" in message, (
        f"{what} was refused, but not by a grant: {refusal.value}"
    )
    assert "append_only_violation" not in message, (
        f"{what} reached the trigger, so the role still holds the privilege"
    )


async def test_the_notifier_role_cannot_grant_itself_more(notify_engine_) -> None:
    """The escape hatch, closed. A role that can widen itself is not restricted."""

    async with notify_engine_.connect() as conn:
        with pytest.raises(ProgrammingError) as refusal:
            await conn.execute(text("grant select on detection.setups to scanner_notify"))

    assert "permission denied" in str(refusal.value).lower()


async def test_the_notifier_role_has_no_elevated_attributes(notify_engine_) -> None:
    """`scanner` is a superuser and this role must not resemble it.

    The notifier runs unattended once a minute forever. The hourly invariants
    check runs as the owner under a human's eye; a forever-loop is not that.
    """
    async with notify_engine_.connect() as conn:
        row = (
            await conn.execute(
                text(
                    "select rolsuper, rolcreatedb, rolcreaterole, rolreplication "
                    "from pg_roles where rolname = current_user"
                )
            )
        ).one()

    assert row == (False, False, False, False), f"scanner_notify holds elevated attributes: {row}"
