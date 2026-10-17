"""Check H2 of ops/soak/invariants.sql, run against a migrated database.

H2 reports an observable (QUARANTINE) symbol with no liquidity history -- a
collector failing for it. It fired on every new Binance listing instead: the
registry syncs at 00:00, the worker's nightly loop writes the first history row
~24 min later, and the hourly invariants run lands at 00:17 in between. The
grace (first_seen_at older than 2 h) is what is tested here.

The statement is read out of ops/soak/invariants.sql itself, so the test runs
exactly what cron runs, not a copy.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import text

pytestmark = pytest.mark.integration

INVARIANTS = Path(__file__).resolve().parents[3] / "ops" / "soak" / "invariants.sql"
LABEL = "select 'H2. observable symbol with no history' as check,"

# Distinct names: the integration database is shared across modules.
NEW_LISTING = "H2GRACENEWUSDT"  # first seen 1 h ago, no history yet
STUCK = "H2GRACESTUCKUSDT"  # first seen 3 h ago, still no history
COLLECTED = "H2GRACEOKUSDT"  # first seen 3 h ago, history written


def _h2_statement() -> str:
    """H2's statement, up to its terminating semicolon -- one outside a `--` comment,
    which is where psql ends it too."""
    sql = INVARIANTS.read_text(encoding="utf-8")
    start = sql.index(LABEL)
    lines = []

    for line in sql[start:].splitlines():
        code = line.split("--", 1)[0]

        if ";" in code:
            lines.append(code[: code.index(";")])
            return "\n".join(lines)

        lines.append(line)

    raise AssertionError("H2 has no terminating semicolon")


async def _insert_symbol(conn, symbol: str, first_seen: datetime) -> None:
    await conn.execute(
        text(
            "insert into market.symbols"
            " (id, venue, exchange_symbol, base_asset, quote_asset, status, first_seen_at)"
            " values (:id, 'binance', :sym, :base, 'USDT', 'QUARANTINE', :seen)"
        ),
        {
            "id": f"TEST-{symbol}",
            "sym": symbol,
            "base": symbol.removesuffix("USDT"),
            "seen": first_seen,
        },
    )


async def test_h2_grace_new_listing_vs_stuck_symbol(engine) -> None:
    now = datetime.now(UTC)

    async with engine.begin() as conn:
        for symbol in (NEW_LISTING, STUCK, COLLECTED):
            for table in ("liquidity_history", "symbols"):
                await conn.execute(
                    text(f"delete from market.{table} where exchange_symbol = :s"), {"s": symbol}
                )

        await _insert_symbol(conn, NEW_LISTING, now - timedelta(hours=1))
        await _insert_symbol(conn, STUCK, now - timedelta(hours=3))
        await _insert_symbol(conn, COLLECTED, now - timedelta(hours=3))
        # Also satisfies H2's "the loop has run at least once" clause.
        await conn.execute(
            text(
                "insert into market.liquidity_history"
                " (exchange_symbol, observed_at, daily_quote_volume, spread_bps, depth_2pct)"
                " values (:s, :at, 1000000, 5, 50000)"
            ),
            {"s": COLLECTED, "at": now - timedelta(hours=2)},
        )

    async with engine.connect() as conn:
        rows = (await conn.execute(text(_h2_statement()))).all()

    flagged = {row[1] for row in rows}

    # H2 caps its output at 10 rows; if the shared database already holds 10
    # other offenders, an absence below would prove nothing.
    assert len(rows) < 10, (
        f"H2 returned its 10-row cap ({sorted(flagged)}); the absence checks are inconclusive"
    )

    assert NEW_LISTING not in flagged, (
        "a listing 1 h old with no history yet must not fire H2 "
        "(the nightly loop has not reached it)"
    )
    assert STUCK in flagged, (
        "a listing 3 h old with still no history must fire H2 (the collector skipped it)"
    )
    assert COLLECTED not in flagged, "a symbol with history must never fire H2"


def test_the_statement_is_read_from_the_shipped_file() -> None:
    statement = _h2_statement()

    assert statement.startswith(LABEL)
    assert "first_seen_at < now() - interval '2 hours'" in statement
