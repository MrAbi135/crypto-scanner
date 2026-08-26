"""§7.5 must be able to build an impulse leg in either direction.

Legs are recomputed on every pass and never persisted, so no query can see
this. It has to run the domain code over the same candles the engine reads,
which is why it runs from the engine image rather than against the database.

**What it is looking for.** `segment_legs` re-anchored `previous_impulse` only
on `LegKind.IMPULSE`, and a counter-direction leg can never *be* an impulse --
escalation and retracement are both tested first and both require
`retrace is not None`, which is exactly the counter-direction case. So the
anchor could only ever move to a leg running the way it already pointed, and
whichever direction printed the window's first impulse held it for the whole
window. Measured on 2026-08-26:

    ETHUSDT H1   IMPULSE  UP  0 / DOWN  9    ESCALATE  UP 17 / DOWN  0
    BTCUSDT H1   IMPULSE  UP 12 / DOWN  0    ESCALATE  UP  0 / DOWN 11
    BTCUSDT H4   IMPULSE  UP 15 / DOWN  0    ESCALATE  UP  0 / DOWN 13

`ESCALATE` filling the locked-out side exactly, and appearing nowhere else, is
the signature: three markets do not agree to that precision.

**Why zero on one side is not the test.** A genuinely one-sided window is
ordinary -- a symbol that only rallied for five hundred candles has no down
impulse and should not. What is not ordinary is zero on one side *while
`ESCALATE` is piled on that same side*, because an escalating leg is one the
market travelled; a real trend simply has fewer legs the other way, not a
drawer full of them under a different name.
"""

from __future__ import annotations

import asyncio
import os
from datetime import UTC, datetime, timedelta

from redis.asyncio import Redis  # noqa: F401  (import proves the image is wired)
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy import text

from scanner.domain.common import Candle
from scanner.domain.common.atr import wilder_atr_series
from scanner.domain.ict.displacement import detect_displacement
from scanner.domain.momentum.legs import LegKind, segment_legs
from scanner.domain.structure import detect_external_swings
from scanner.shared import Timeframe

WINDOW = 500

# Only where setups form. M5 and M15 carry the same code and would triple the
# runtime to re-prove the same property.
CONTEXTS = (Timeframe.H1, Timeframe.H4)


def _dsn() -> str:
    raw = os.environ.get("SCANNER_DB_DSN") or os.environ["DATABASE_URL"]

    return raw.replace("postgresql://", "postgresql+asyncpg://", 1)


async def _symbols(conn) -> tuple[str, ...]:
    rows = await conn.execute(text("select distinct symbol from market.candles order by 1"))

    return tuple(r[0] for r in rows)


async def _window(conn, symbol: str, timeframe: Timeframe) -> list[Candle]:
    rows = await conn.execute(
        text(
            "select open_time, open, high, low, close, volume, quote_volume,"
            "       taker_buy_volume, trade_count"
            "  from market.candles"
            " where symbol = :s and timeframe = :t"
            " order by open_time desc limit :n"
        ),
        {"s": symbol, "t": timeframe.value, "n": WINDOW + 1},
    )

    return [
        Candle(
            symbol=symbol,
            timeframe=timeframe,
            open_time=r[0],
            open=r[1],
            high=r[2],
            low=r[3],
            close=r[4],
            volume=r[5],
            quote_volume=r[6],
            taker_buy_volume=r[7],
            trade_count=r[8],
            source="binance",
        )
        for r in reversed(list(rows))
    ]


def _counts(series: list[Candle]) -> dict[str, int]:
    atrs = wilder_atr_series(series)

    displacement = frozenset(
        index
        for index, atr in enumerate(atrs)
        if atr is not None and atr > 0 and detect_displacement(series, index, atr=atr) is not None
    )

    legs = segment_legs(series, detect_external_swings(series), displacement)

    tally: dict[str, int] = {}

    for leg in legs:
        key = f"{leg.kind.value}/{leg.direction}"
        tally[key] = tally.get(key, 0) + 1

    return tally


async def main() -> None:
    engine = create_async_engine(_dsn())
    violations = 0

    async with engine.connect() as conn:
        for symbol in await _symbols(conn):
            for timeframe in CONTEXTS:
                series = await _window(conn, symbol, timeframe)

                if len(series) < WINDOW:
                    print(f"{symbol:8} {timeframe.value:3} only {len(series)} candles, skipped")
                    continue

                tally = _counts(series)

                up = tally.get(f"{LegKind.IMPULSE.value}/UP", 0)
                down = tally.get(f"{LegKind.IMPULSE.value}/DOWN", 0)
                esc_up = tally.get(f"{LegKind.ESCALATE.value}/UP", 0)
                esc_down = tally.get(f"{LegKind.ESCALATE.value}/DOWN", 0)

                line = (
                    f"{symbol:8} {timeframe.value:3} "
                    f"IMPULSE up={up:<3} down={down:<3}  ESCALATE up={esc_up:<3} down={esc_down}"
                )

                # Zero impulses one way while escalations pile up that same
                # way. Either half alone is an ordinary market.
                starved = (up == 0 and esc_up >= 5) or (down == 0 and esc_down >= 5)

                if starved:
                    print(f"VIOLATION {line}")
                    violations += 1
                else:
                    print(line)

    await engine.dispose()

    stamp = datetime.now(UTC).replace(microsecond=0).isoformat()
    print(f"checked at {stamp}; {violations} violation(s)")


asyncio.run(main())
