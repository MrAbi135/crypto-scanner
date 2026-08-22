"""Parse and validate the ingested context set (Sprint S3b).

Separated from the settings class because the ladder rule below is doctrine,
not configuration plumbing, and it needs testing on its own.
"""

from __future__ import annotations

from scanner.shared import Timeframe
from scanner.shared.errors import ValidationError

# Bottom-up. Mirrors `ict_interaction_replay._lower_timeframe`, which is the
# reason the rule exists.
_LADDER: tuple[Timeframe, ...] = (
    Timeframe.M5,
    Timeframe.M15,
    Timeframe.H1,
    Timeframe.H4,
    Timeframe.D1,
    Timeframe.W1,
)


def higher_timeframe(timeframe: Timeframe) -> Timeframe | None:
    """The timeframe one rung above, or None at the top of the ladder.

    §8.4's HTF alignment is defined against the next TF up, and W1 has no next
    TF up -- so `None` here is a real answer, not a lookup failure.
    """
    position = _LADDER.index(timeframe)

    if position + 1 >= len(_LADDER):
        return None

    return _LADDER[position + 1]


def parse_symbols(raw: str) -> tuple[str, ...]:
    symbols = tuple(part.strip().upper() for part in raw.split(",") if part.strip())

    if not symbols:
        raise ValidationError("at least one ingest symbol is required")

    if len(set(symbols)) != len(symbols):
        raise ValidationError(f"duplicate ingest symbols: {raw}")

    return symbols


def parse_timeframes(raw: str) -> tuple[Timeframe, ...]:
    """Parse the ladder, and refuse one with a hole in it.

    A timeframe whose lower neighbour is missing produces **zero zone
    confirmations** -- `ict_interaction_replay` reads the timeframe below to
    find the LTF break that confirms a reaction, and an empty series there
    means the branch never runs. Nothing fails; the count is simply always 0.

    Found on 2026-08-17 by running the engine on real BTC H1 with no M15
    ingested: 3581 touches, 476 rejections, 0 confirmations. Backfilling M15
    turned that into 235. The rule is enforced at boot so nobody rediscovers it
    by reading a suspicious dashboard weeks later.
    """
    parsed = tuple(Timeframe.parse(part.strip()) for part in raw.split(",") if part.strip())

    if not parsed:
        raise ValidationError("at least one ingest timeframe is required")

    if len(set(parsed)) != len(parsed):
        raise ValidationError(f"duplicate ingest timeframes: {raw}")

    ordered = tuple(tf for tf in _LADDER if tf in parsed)

    highest = _LADDER.index(ordered[-1])
    lowest = _LADDER.index(ordered[0])

    expected = set(_LADDER[lowest : highest + 1])

    missing = expected - set(ordered)

    if missing:
        gap = ", ".join(tf.value for tf in _LADDER if tf in missing)

        raise ValidationError(
            f"ingest timeframes have a hole in the ladder: {gap} missing between "
            f"{ordered[0].value} and {ordered[-1].value}. Higher timeframes read "
            f"the one below them for zone confirmation, which silently yields "
            f"zero when it is empty."
        )

    return ordered


def stream_names(
    symbols: tuple[str, ...],
    timeframes: tuple[Timeframe, ...],
    *,
    trades: bool = False,
) -> tuple[str, ...]:
    """Binance combined-stream names for every context.

    `trades` adds one `@aggTrade` stream per *symbol*, not per context: the
    tape is a property of the symbol, and subscribing per timeframe would
    deliver every print four times over.
    """
    klines = tuple(
        f"{symbol}@kline_{_BINANCE_INTERVAL[timeframe]}"
        for symbol in symbols
        for timeframe in timeframes
    )

    if not trades:
        return klines

    return klines + tuple(f"{symbol}@aggTrade" for symbol in symbols)


_BINANCE_INTERVAL: dict[Timeframe, str] = {
    Timeframe.M5: "5m",
    Timeframe.M15: "15m",
    Timeframe.H1: "1h",
    Timeframe.H4: "4h",
    Timeframe.D1: "1d",
    Timeframe.W1: "1w",
}
