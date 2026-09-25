"""Check A's break test, with SLS §3.5's `ε` -- which it did not have.

Check A asks whether price closed clean through its own external bracket in
the trend's direction while no break was recorded. It asked that in SQL, as a
plain `close < lo` / `close > hi`. §3.5 does not: its inputs list `ε`, its
detection logic is `Cl[i] > H[swing] + ε`, and its edge case (2) says in so
many words that a "break of a level within `ε`" is **not a break**. So the
check demanded an event the doctrine forbids.

Measured on 2026-09-24, BNBUSDT M15: close 765.23 against a confirmed
external low of 765.29, a penetration of **0.06** where `ε` was
`TOLERANCE_ATR x ATR = 0.05 x 1.9514 = 0.0976`. `detect_bos` returns None
there and is right to; check A flagged a missing BOS_DOWN for two runs.

**Why this runs from the engine image instead of in SQL.** `ε` is
`P.global.tolerance_atr x ATR`, and ATR is Wilder-smoothed -- a recurrence
with a seeding region, not an expression. A second implementation of it in
SQL would be a new way for the check and the engine to disagree, which is
this suite's oldest recurring defect: a check that has stopped measuring what
it names while still printing a plausible line. This imports
`wilder_atr_series`, `TOLERANCE_ATR` and `detect_bos` themselves, so there is
exactly one definition of a break and the check cannot drift from it.

**What it deliberately does not touch.** §3.4's idle test (check A's `inside`
bucket) is a containment question -- did every close sit inside the bracket --
not a break test, and §3.5's tolerance has no business in it. That bucket is
left exactly as it was.

Output, one line per context, parsed by check_invariants.sh:

    TOLERANCE <symbol> <timeframe> <pen> <eps> <break:yes|no>

`pen` is the largest penetration beyond the gate-side level among closes made
after that level existed; `break` is whether `detect_bos` confirms any of
them. A context with nothing to say prints zeros and `no`.
"""

from __future__ import annotations

import asyncio
import os
from decimal import Decimal

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from scanner.application.detection.state import EngineStateManager
from scanner.application.detection.structure_shift_replay import (
    STRUCTURE_SHIFT_ALGO_VERSION,
)
from scanner.application.parameters import TOLERANCE_ATR
from scanner.domain.common import Candle
from scanner.domain.common.atr import wilder_atr_series
from scanner.domain.structure import (
    IDLE_CANDLES,
    BreakDirection,
    SwingKind,
    SwingStrength,
    detect_bos,
    detect_external_swings,
    swing_window,
)
from scanner.infrastructure.redis.client import build_redis
from scanner.infrastructure.redis.engine_state import RedisEngineStateStore
from scanner.shared import Timeframe

WINDOW = 500

K_EXTERNAL = swing_window(SwingStrength.EXTERNAL)

# The same four check A walks. Unlike the leg check this is one ATR pass per
# context, so there is no reason to narrow it.
CONTEXTS = (Timeframe.M5, Timeframe.M15, Timeframe.H1, Timeframe.H4)

# Check A reads the shift engine's trend, and this must read the same one or
# the two would disagree about which gate is open. Whether that is the right
# engine to read is a separate, open doctrine question -- see the tracker's
# row 19. Nothing here decides it.
GATES = {
    "BULLISH": (BreakDirection.UP, SwingKind.HIGH),
    "BULLISH_CAUTION": (BreakDirection.UP, SwingKind.HIGH),
    "BEARISH": (BreakDirection.DOWN, SwingKind.LOW),
    "BEARISH_CAUTION": (BreakDirection.DOWN, SwingKind.LOW),
}


def _dsn() -> str:
    raw = os.environ.get("SCANNER_DB_DSN") or os.environ["DATABASE_URL"]

    return raw.replace("postgresql://", "postgresql+asyncpg://", 1)


async def _symbols(conn) -> tuple[str, ...]:
    """The symbols the running engine scans, taken from its own configuration."""
    configured = os.environ.get("SCANNER_INGEST_SYMBOLS", "")
    wanted = tuple(s.strip() for s in configured.split(",") if s.strip())

    if not wanted:
        return ()

    rows = await conn.execute(
        text(
            "select distinct symbol from market.candles"
            " where symbol = any(:wanted) order by 1"
        ),
        {"wanted": list(wanted)},
    )

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


def _verdict(series: list[Candle], trend: str) -> tuple[Decimal, Decimal, bool]:
    """Largest penetration beyond the gate-side level, its `ε`, and §3.5's answer."""
    gate = GATES.get(trend)

    if gate is None:
        return Decimal(0), Decimal(0), False

    direction, kind = gate
    last = len(series) - 1

    levels = [
        swing
        for swing in detect_external_swings(series)
        if swing.kind is kind and swing.index + K_EXTERNAL <= last
    ]

    if not levels:
        return Decimal(0), Decimal(0), False

    level = max(levels, key=lambda s: s.index)
    atrs = wilder_atr_series(series)

    # Only closes made after the level existed. #220 fixed exactly that trap
    # in the SQL, and it is available here in the same shape.
    start = max(level.index + 1, len(series) - IDLE_CANDLES)

    worst = Decimal(0)
    worst_eps = Decimal(0)
    broke = False

    for index in range(start, len(series)):
        candle = series[index]
        atr = atrs[index]
        eps = TOLERANCE_ATR * atr if atr is not None else Decimal(0)

        if direction is BreakDirection.UP:
            penetration = candle.close - level.price
        else:
            penetration = level.price - candle.close

        if penetration <= 0:
            continue

        if detect_bos(candle, level, direction=direction, epsilon=eps) is not None:
            broke = True

        if penetration > worst:
            worst = penetration
            worst_eps = eps

    return worst, worst_eps, broke


async def main() -> None:
    engine = create_async_engine(_dsn())
    redis = build_redis(os.environ["SCANNER_REDIS_URL"])
    states = EngineStateManager(RedisEngineStateStore(redis))

    async with engine.connect() as conn:
        for symbol in await _symbols(conn):
            for timeframe in CONTEXTS:
                saved = await states.load(
                    symbol, timeframe.value, STRUCTURE_SHIFT_ALGO_VERSION
                )

                if saved is None:
                    continue

                series = await _window(conn, symbol, timeframe)

                if len(series) < IDLE_CANDLES + K_EXTERNAL:
                    continue

                pen, eps, broke = _verdict(series, saved.trend_state)

                print(
                    f"TOLERANCE {symbol} {timeframe.value} {pen} {eps} "
                    f"{'yes' if broke else 'no'}"
                )

    await engine.dispose()
    await redis.aclose()


if __name__ == "__main__":
    # Guarded, unlike leg_invariant.py, so `_verdict` can be driven by a test.
    # The container still invokes this as `python /tmp/tol.py`, which is
    # `__main__`, so the entry point is unchanged.
    asyncio.run(main())
