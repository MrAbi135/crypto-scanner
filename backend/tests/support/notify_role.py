"""The interim notifier's role: the operator's SQL applied to a real role.

The sibling of `least_privilege.py`, and deleted with the notifier at S18.
`test_notify_role_pg.py` attacks the role this builds. It must be built from
`ops/db/notify-role.sql` -- the same file an operator applies to a real
database -- or the test would be attacking a layer that does not exist in
production.
"""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncEngine

from scanner.infrastructure.persistence.database import build_engine

NOTIFY_ROLE_SQL = Path(__file__).resolve().parents[3] / "ops" / "db" / "notify-role.sql"

NOTIFY_ROLE = "scanner_notify"


def notify_role_statements() -> list[str]:
    """The operator's SQL, split the way psql would run it."""

    body = "\n".join(
        line
        for line in NOTIFY_ROLE_SQL.read_text(encoding="utf-8").splitlines()
        if not line.startswith("--")
    )

    return [statement.strip() for statement in body.split(";") if statement.strip()]


async def notify_engine(pg_dsn: str, owner: AsyncEngine) -> AsyncEngine:
    """Create the notifier role if needed, apply the grants, connect as it.

    A password is set here and not in production. The real role is reached
    through the container's unix socket, which `pg_hba.conf` trusts; the test
    connects over TCP, which does not. The grants -- the thing under test --
    are identical either way.
    """

    async with owner.begin() as conn:
        # The container is shared across the session, so both halves are
        # idempotent: the role may already exist and the grants re-apply cleanly.
        await conn.execute(
            text(
                f"""
                DO $$ BEGIN
                  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{NOTIFY_ROLE}') THEN
                    CREATE ROLE {NOTIFY_ROLE}
                      LOGIN PASSWORD '{NOTIFY_ROLE}'
                      NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION;
                  END IF;
                END $$
                """
            )
        )

        for statement in notify_role_statements():
            await conn.execute(text(statement))

    dsn = make_url(pg_dsn).set(username=NOTIFY_ROLE, password=NOTIFY_ROLE)

    return build_engine(dsn.render_as_string(hide_password=False), pool_size=2)
