"""G1b evidence: the doctrine renders for a symbol nobody picked.

Roadmap §9 asks that "the chart renders live structure/liquidity/zone objects
for a symbol the developer did not pre-select". The whole weight of that
criterion is in *did not pre-select*: a chart that works on the symbol the
engine was tuned against proves only that it was tuned.

So the choice is made by a rule fixed before the outcome is known, and the rule
is stated here rather than applied by hand:

    USDT symbols in the venue registry, not DELISTED,
    ordered by exchange_symbol, take the N nearest the middle of the list.

The middle is arbitrary on purpose. Nothing about a symbol's liquidity, name or
chart is consulted, and re-running this picks the same symbols -- so a poor
result is a result, not a reason to pick again.

    uv run --project backend python scripts/g1b_unselected_symbol.py --plan
    uv run --project backend python scripts/g1b_unselected_symbol.py --backfill
    uv run --project backend python scripts/g1b_unselected_symbol.py --verify

`--plan` names the symbols and stops, so the choice can be recorded before any
data exists for them. `--backfill` fetches the ladder. `--verify` reports what
detection produced, which is the evidence the criterion asks for.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import subprocess
import sys
from datetime import UTC, datetime, timedelta

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

# §5.9 confirmation reads the timeframe below, so the ladder is filled
# bottom-up or the top of it silently yields nothing.
# Minutes per candle, so each rung can be given a start that yields roughly
# the same history. One shared start would fetch 23,000 M5 candles to get 480
# H4 ones -- most of an hour of API calls for the wrong shape of data.
LADDER: dict[str, int] = {"M5": 5, "M15": 15, "H1": 60, "H4": 240}

# §1.9 gates detection at 300 closed candles; the extra is headroom so the
# first live close is not also the first warm one.
BACKFILL_CANDLES = 600

DEFAULT_COUNT = 2


def _log(message: str) -> None:
    print(message, flush=True)


def _fail(message: str) -> None:
    print("FAILED: " + message, file=sys.stderr, flush=True)
    raise SystemExit(1)


async def _pick(dsn: str, count: int) -> list[str]:
    engine = create_async_engine(dsn)

    try:
        async with engine.connect() as conn:
            rows = (
                await conn.execute(
                    text(
                        "select exchange_symbol from market.symbols "
                        "where quote_asset = 'USDT' and status <> 'DELISTED' "
                        "order by exchange_symbol"
                    )
                )
            ).all()
    finally:
        await engine.dispose()

    symbols = [row[0] for row in rows]

    if len(symbols) < count:
        raise SystemExit(f"registry holds only {len(symbols)} eligible symbols")

    middle = len(symbols) // 2

    return symbols[middle : middle + count]


async def _counts(dsn: str, symbol: str) -> dict[str, int]:
    engine = create_async_engine(dsn)

    queries = {
        "candles": "select count(*) from market.candles where symbol = :s",
        "swings": (
            "select count(*) from detection.engine_events "
            "where symbol = :s and event_type like 'SWING_%'"
        ),
        "structure": (
            "select count(*) from detection.engine_events "
            "where symbol = :s and event_type like 'STRUCTURE_%'"
        ),
        "pools": "select count(*) from detection.liquidity_pools where symbol = :s",
        "sweeps": (
            "select count(*) from detection.liquidity_transitions "
            "where symbol = :s and reason = 'liquidity_sweep'"
        ),
        "zones": "select count(*) from detection.ict_zones where symbol = :s",
    }

    try:
        async with engine.connect() as conn:
            return {
                name: int((await conn.execute(text(sql), {"s": symbol})).scalar_one())
                for name, sql in queries.items()
            }
    finally:
        await engine.dispose()


def _run(args: list[str]) -> int:
    return subprocess.run(args, check=False).returncode


def _cli(*args: str) -> list[str]:
    """`scanner.interfaces.cli` is a package with no entry point.

    Its `build_parser` is imported by `scanner.runtime.cli`, which is what the
    repo's own `scripts/cli.ps1` runs. Pointed at the package, every backfill
    exited 1 with "cannot be directly executed".
    """
    return [sys.executable, "-m", "scanner.runtime.cli", *args]


async def main() -> None:
    parser = argparse.ArgumentParser(description="G1b unselected-symbol proof")
    parser.add_argument("--count", type=int, default=DEFAULT_COUNT)
    parser.add_argument("--plan", action="store_true")
    parser.add_argument("--backfill", action="store_true")
    parser.add_argument("--verify", action="store_true")
    parser.add_argument(
        "--now",
        default=None,
        help="ISO date the ladder is measured back from (default: today, UTC)",
    )

    args = parser.parse_args()

    dsn = os.environ["SCANNER_DB_DSN"]

    symbols = await _pick(dsn, args.count)

    _log("Rule: USDT, not DELISTED, ordered by exchange_symbol, middle of the list.")
    _log(f"Picked: {', '.join(symbols)}\n")

    if args.plan:
        return

    if args.backfill:
        anchor = (
            datetime.fromisoformat(args.now)
            if args.now
            else datetime.now(UTC)
        )

        failures: list[str] = []

        for symbol in symbols:
            for timeframe, minutes in LADDER.items():
                start = (anchor - timedelta(minutes=minutes * BACKFILL_CANDLES)).date()

                _log(f"backfill {symbol} {timeframe} from {start}")

                code = _run(
                    _cli(
                        "backfill",
                        "--symbol",
                        symbol,
                        "--timeframe",
                        timeframe,
                        "--start",
                        start.isoformat(),
                    )
                )

                if code != 0:
                    failures.append(f"{symbol} {timeframe}")

                    _log(f"  ! backfill exited {code}")

        if failures:
            # The first run of this script logged eight failed backfills and
            # still exited 0, which is the shape of every defect this project
            # keeps finding: a report that reads like a pass.
            _fail(f"{len(failures)} backfills failed: {', '.join(failures)}")

    if args.verify:
        for symbol in symbols:
            counts = await _counts(dsn, symbol)

            _log(f"{symbol}: " + "  ".join(f"{k}={v}" for k, v in counts.items()))

            drawable = counts["swings"] + counts["pools"] + counts["zones"]

            _log(f"  drawable objects: {drawable}")


if __name__ == "__main__":
    asyncio.run(main())
