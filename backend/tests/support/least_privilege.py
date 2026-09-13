"""DDD layer (a) for integration tests: the operator's SQL applied to a real role.

One definition, shared. `test_grant_layer_pg.py` attacks the restricted role;
`test_publish_path_pg.py` publishes through it. Both must run against the
same grants an operator applies to a real database -- `ops/db/least-privilege-role.sql`
-- or one of them would be testing a layer that does not exist.
"""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncEngine

from scanner.infrastructure.persistence.database import build_engine

LEAST_PRIVILEGE_SQL = (
    Path(__file__).resolve().parents[3] / "ops" / "db" / "least-privilege-role.sql"
)

APP_ROLE = "scanner_app"


def least_privilege_statements() -> list[str]:
    """The operator's SQL, split the way psql would run it."""

    body = "\n".join(
        line
        for line in LEAST_PRIVILEGE_SQL.read_text(encoding="utf-8").splitlines()
        if not line.startswith("--")
    )

    return [statement.strip() for statement in body.split(";") if statement.strip()]


async def restricted_engine(pg_dsn: str, owner: AsyncEngine) -> AsyncEngine:
    """Create the application role if needed, apply the grants, connect as it."""

    async with owner.begin() as conn:
        # The container is shared across the session, so both halves are
        # idempotent: the role may already exist and the grants re-apply cleanly.
        await conn.execute(
            text(
                f"""
                DO $$ BEGIN
                  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{APP_ROLE}') THEN
                    CREATE ROLE {APP_ROLE} LOGIN PASSWORD '{APP_ROLE}';
                  END IF;
                END $$
                """
            )
        )

        for statement in least_privilege_statements():
            await conn.execute(text(statement))

    dsn = make_url(pg_dsn).set(username=APP_ROLE, password=APP_ROLE)

    return build_engine(dsn.render_as_string(hide_password=False), pool_size=2)
