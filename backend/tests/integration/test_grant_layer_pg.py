"""DDD layer (a), attacked: the application role cannot rewrite the sealed record.

`test_detection_persistence_pg.py` attacks layer (b) -- the append-only
triggers -- and does it as the owner, because that is the role the compose
stack connects as. Every one of those attacks is refused, and every one of them
is refused by a trigger. None of them asks the question DDD actually poses:
*"No UPDATE/DELETE path exists at any privilege level used by the
application."* An owner has such a path. It is one statement long:

    ALTER TABLE detection.signals DISABLE TRIGGER trg_signals_append_only;

This file applies `ops/db/least-privilege-role.sql` -- the same file an operator
applies to a real database -- to a restricted role, connects as that role, and
repeats the attacks. Three things have to be true for layer (a) to exist:

1. the writes are refused **by a grant**, before any trigger fires -- so the
   message is a permission denial, not `append_only_violation`;
2. the escape hatch is gone: the role cannot disable the trigger at all;
3. nothing the application legitimately does is refused -- it can still insert
   and read signals, and still update every table that is not sealed.

The third is the one a careless version of this layer gets wrong, and it would
get it wrong in production: a grant that also refused INSERT would pass every
attack here and stop the engine publishing anything.

Requires Docker (testcontainers). Run: pytest -m integration tests/integration
"""

from __future__ import annotations

import pytest

pytest.importorskip("testcontainers")

from sqlalchemy import text

from scanner.application.immutability_verification import verify_immutability_guards
from scanner.infrastructure.persistence.database import build_session_factory
from scanner.infrastructure.persistence.immutability_inspector import PgImmutabilityInspector
from tests.support.least_privilege import APP_ROLE as _ROLE
from tests.support.least_privilege import LEAST_PRIVILEGE_SQL as _SQL
from tests.support.least_privilege import least_privilege_statements as _statements

pytestmark = pytest.mark.integration

_SEALED = ("signals", "signal_transitions", "signal_outcomes")

# `app_engine` -- the connection as the restricted role, after the operator's
# SQL has run -- lives in conftest.py, because test_publish_path_pg.py needs
# the identical role to publish through.


async def test_the_operator_sql_is_the_file_this_test_reads() -> None:
    """A guard against the two copies drifting: there is exactly one."""

    assert _SQL.is_file(), f"missing {_SQL}"
    assert any("REVOKE UPDATE, DELETE, TRUNCATE" in s for s in _statements())


@pytest.mark.parametrize("table", _SEALED)
async def test_the_app_role_cannot_update_or_delete_the_sealed_tables(
    app_engine,
    table: str,
) -> None:
    """Refused by the grant, not by the trigger behind it.

    Asserting only "an error happened" would pass against layer (b) alone, and
    this file would then be a second copy of the trigger tests wearing a new
    name. The message is what distinguishes the layers: a permission denial is
    raised before the executor starts, so the trigger never gets the chance.
    """

    for statement in (
        f"UPDATE detection.{table} SET signal_id = signal_id",
        f"DELETE FROM detection.{table}",
    ):
        async with app_engine.begin() as conn:
            with pytest.raises(Exception) as raised:
                await conn.execute(text(statement))

        message = str(raised.value)

        assert "permission denied" in message, (
            f"{statement!r} was not refused by a grant: {message}"
        )
        assert "append_only_violation" not in message, (
            f"{statement!r} reached the trigger, so layer (a) did not refuse it"
        )


@pytest.mark.parametrize("table", _SEALED)
async def test_the_app_role_cannot_truncate_the_sealed_tables(app_engine, table: str) -> None:
    async with app_engine.begin() as conn:
        with pytest.raises(Exception, match="permission denied"):
            await conn.execute(text(f"TRUNCATE detection.{table} CASCADE"))


@pytest.mark.parametrize("table", _SEALED)
async def test_the_app_role_cannot_disable_the_guard(app_engine, table: str) -> None:
    """The attack the owner role makes possible, and the reason for the layer.

    An owner can switch the trigger off and then write whatever it likes; the
    boot check would notice on the next restart, and not before. The
    restricted role cannot issue the statement at all.
    """

    trigger = f"trg_{table}_append_only"

    async with app_engine.begin() as conn:
        with pytest.raises(Exception, match="must be owner"):
            await conn.execute(text(f"ALTER TABLE detection.{table} DISABLE TRIGGER {trigger}"))


async def test_the_app_role_keeps_every_privilege_the_application_uses(app_engine) -> None:
    """Layer (a) must refuse nothing the engine and API legitimately do.

    Asked of the catalog rather than by writing rows, because `has_table_privilege`
    reports the effective answer for every table at once -- including the
    `identity` schema, which the runbook's first draft of this SQL omitted and
    a cut-over on that draft would have locked the API out of.
    """

    async with app_engine.connect() as conn:
        sealed = {
            (table, privilege): (
                await conn.execute(
                    text("SELECT has_table_privilege(:role, :table, :privilege)"),
                    {"role": _ROLE, "table": f"detection.{table}", "privilege": privilege},
                )
            ).scalar_one()
            for table in _SEALED
            for privilege in ("SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE")
        }

        unsealed = (
            (
                await conn.execute(
                    text(
                        """
                    SELECT n.nspname || '.' || c.relname
                    FROM pg_class c
                    JOIN pg_namespace n ON n.oid = c.relnamespace
                    WHERE c.relkind IN ('r', 'p')
                      AND n.nspname IN ('detection', 'market', 'ops', 'identity')
                      AND NOT (n.nspname = 'detection' AND c.relname = ANY(:sealed))
                      AND NOT (
                        has_table_privilege(:role, c.oid, 'SELECT')
                        AND has_table_privilege(:role, c.oid, 'INSERT')
                        AND has_table_privilege(:role, c.oid, 'UPDATE')
                        AND has_table_privilege(:role, c.oid, 'DELETE')
                      )
                    """
                    ),
                    {"role": _ROLE, "sealed": list(_SEALED)},
                )
            )
            .scalars()
            .all()
        )

    for table in _SEALED:
        assert sealed[(table, "SELECT")], f"{table}: the role cannot read the record it publishes"
        assert sealed[(table, "INSERT")], (
            f"{table}: the role cannot publish -- the engine would stop"
        )
        assert not sealed[(table, "UPDATE")], f"{table}: UPDATE is still granted"
        assert not sealed[(table, "DELETE")], f"{table}: DELETE is still granted"
        assert not sealed[(table, "TRUNCATE")], f"{table}: TRUNCATE is still granted"

    assert not unsealed, f"the application role lost ordinary access to: {sorted(unsealed)}"


async def test_the_boot_check_stops_reporting_the_layer_absent(app_engine) -> None:
    """The engine's own verdict, read from the restricted connection.

    Every boot on the owner role logs `immutability_grant_layer_absent`. On this
    role the same check must say the layer is present -- and the trigger half
    must still pass, because layer (a) is added to (b), not swapped for it.
    """

    inspector = PgImmutabilityInspector(build_session_factory(app_engine))

    report = await verify_immutability_guards(inspector)

    assert report.guarded == _SEALED
    assert report.role_bypasses_grants is False
