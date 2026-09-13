"""Canonical hypothesis strategies (S0.3 §3).

The generators every future engine/golden test reuses — decimals-as-strings,
timeframes, ULIDs, UTC datetimes.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from hypothesis import strategies as st

from scanner.domain.common import Candle, CandleSource
from scanner.shared.ids import new_ulid
from scanner.shared.timeutil import Timeframe


def decimal_strings(min_value: str = "-1e15", max_value: str = "1e15") -> st.SearchStrategy[str]:
    return st.decimals(
        allow_nan=False,
        allow_infinity=False,
        min_value=Decimal(min_value),
        max_value=Decimal(max_value),
    ).map(str)


def timeframes() -> st.SearchStrategy[Timeframe]:
    return st.sampled_from(list(Timeframe))


def epoch_millis() -> st.SearchStrategy[int]:
    return st.integers(min_value=0, max_value=(1 << 48) - 1)


def ulids() -> st.SearchStrategy[str]:
    return epoch_millis().map(lambda ms: new_ulid(timestamp_ms=ms))


def utc_datetimes() -> st.SearchStrategy[object]:
    return st.datetimes(timezones=st.just(UTC))


_SERIES_ORIGIN = datetime(2026, 1, 5, tzinfo=UTC)  # Monday — valid for every TF


def equal_highs_series(
    *,
    timeframe: Timeframe = Timeframe.H1,
    symbol: str = "PROPEQ",
) -> st.SearchStrategy[list[Candle]]:
    """A series that prints three to six near-equal swing highs in a row.

    Built for one property: §4.3's "later qualifying members join
    incrementally (join events are appends, never rewrites)". That clause only
    has teeth when a chain *grows* between a prefix and the full series, and
    neither general strategy produces growth often enough to test it. Measured
    over 300 random prefix splits: `candle_series` grew a chain **5** times;
    this grows one **158** times.

    Each peak is a pyramid -- an ascent of 1.5 per candle, a single top, a
    matching descent to a shared valley -- so every top is strictly above the
    five candles either side and confirms as an external swing. Candles span
    4.0 and step at most 1.5, so a gap term never exceeds the candle's own
    range and ATR settles near 4.0, putting §4.3's tolerance near 0.05 x 4 =
    0.20. Tops are jittered by 0, 0.05 or 0.10 -- always inside that tolerance
    pairwise -- and valleys sit 10.5 under them, far past the 0.5 x ATR depth
    §4.3 requires between members. Sixteen flat candles lead in, because the
    cluster test needs ATR at the second member's confirmation and Wilder
    seeds over fourteen.

    Hypothesis draws the peak count and every jitter, so it still explores
    which members join and in what order; what it does not explore is whether
    a cluster exists at all, because that is not what the property asks.
    """

    rise = 7
    lead = 16

    def bar(level: Decimal, index: int) -> Candle:
        return Candle(
            symbol=symbol,
            timeframe=timeframe,
            open_time=_SERIES_ORIGIN + timeframe.duration * index,
            open=level - Decimal("0.25"),
            high=level + Decimal(2),
            low=level - Decimal(2),
            close=level + Decimal("0.25"),
            volume=Decimal(100),
            quote_volume=Decimal(10_000),
            taker_buy_volume=Decimal(50),
            trade_count=10,
            source=CandleSource.BACKFILL,
        )

    def build(jitters: list[int]) -> list[Candle]:
        valley = Decimal(1000)
        step = Decimal("1.5")
        top = valley + step * rise
        levels: list[Decimal] = [valley] * lead

        for jitter in jitters:
            levels += [valley + step * i for i in range(1, rise)]
            levels.append(top + Decimal("0.05") * jitter)
            levels += [top - step * i for i in range(1, rise + 1)]

        return [bar(level, index) for index, level in enumerate(levels)]

    return st.lists(
        st.integers(min_value=0, max_value=2),
        min_size=3,
        max_size=6,
    ).map(build)


def mss_then_reclaim_series(
    *,
    timeframe: Timeframe = Timeframe.H1,
    symbol: str = "PROPMSS",
    reclaim_delays: st.SearchStrategy[int | None] | None = None,
) -> st.SearchStrategy[tuple[list[Candle], int | None]]:
    """A series that confirms a bearish MSS and then, maybe, takes it back.

    Built for §3.6's invalidation: *"within 10 candles of an MSS, a close back
    beyond the pre-MSS extreme demotes the new trend to RANGING and marks the
    MSS low_quality"*. The general strategies almost never reach it. Measured
    on 400 `walking_candle_series`: 647 MSS, **21** invalidations -- so a
    25-example replay property usually never saw one inside a prefix. Live, it
    is not rare (16 of 193 MSS on the VM); only the generator was thin.

    Returns the candles and the drawn reclaim delay: the number of candles
    after the MSS on which a close first lands above the pre-MSS extreme, or
    ``None`` when price never comes back. Doctrine therefore expects an
    invalidation exactly when the delay is 1 to 10.

    The shape, each bar spanning 4.0 around its level like `equal_highs_series`:

    * 300 flat candles, so §1.9's warm floor is cleared and ATR settles at 4.0;
    * four pyramid up-legs -- a 9 to 12 candle rise at 1.5 per candle, a 7
      candle pullback -- so every top and valley confirms as an external swing
      and the valleys rise, printing the HH/HL pairs §3.4 needs for BULLISH;
    * a 5 or 6 candle failure rally that tops out under the last high: a lower
      high, §3.6 origin 2(b), whose price becomes the pre-MSS extreme (the
      structure-shift harness has no liquidity evidence, so a sweep origin is
      unreachable here by design);
    * five falling candles, then one wide bearish candle closing 3 to 8 under
      the protected higher low -- the CHoCH and its displacement -- and a close
      under that candle's low 1 to 3 candles later: the MSS;
    * then the drawn reclaim, and a flat tail.

    Measured before the property was written, 150 draws: the planned CHoCH and
    MSS published in 150, an invalidation in 75, and in all 150 the engine's
    answer matched the 10-candle rule.

    **The window's edges are not left to chance.** A rule that is wrong by one
    candle -- the window nine or eleven long, or the candle straight after the
    MSS refused -- differs from the right one at a single reclaim delay: 1, 10
    or 11. Drawn uniformly over 1-14 with a coin-flip reclaim, each turned up
    in about 3.6% of examples, and a mutation battery showed the cost: across
    three isolated runs of 40 examples, "window of nine" and "not on the first
    candle" each survived once. Leaning the draws onto those delays still left
    11 at 7.5%, and "window of eleven" survived one run of three. So
    ``reclaim_delays`` lets a test pin the delay -- ``None`` is "never comes
    back" -- and the edges get a test of their own; left unset, the draws still
    lean on the edges but range over the whole window.
    """

    step = Decimal("1.5")
    pullback = 7

    def bar(index: int, *, open_: Decimal, high: Decimal, low: Decimal, close: Decimal) -> Candle:
        return Candle(
            symbol=symbol,
            timeframe=timeframe,
            open_time=_SERIES_ORIGIN + timeframe.duration * index,
            open=open_,
            high=high,
            low=low,
            close=close,
            volume=Decimal(100),
            quote_volume=Decimal(10_000),
            taker_buy_volume=Decimal(50),
            trade_count=10,
            source=CandleSource.BACKFILL,
        )

    def build(
        drawn: tuple[int, int, int, int, int | None, int],
    ) -> tuple[list[Candle], int | None]:
        rise, failure_rally, drop, followthrough_after, reclaim_delay, overshoot = drawn
        candles: list[Candle] = []

        def level_bar(level: Decimal) -> None:
            candles.append(
                bar(
                    len(candles),
                    open_=level - Decimal("0.25"),
                    high=level + 2,
                    low=level - 2,
                    close=level + Decimal("0.25"),
                )
            )

        def flat_bar(level: Decimal) -> None:
            candles.append(
                bar(len(candles), open_=level, high=level + 1, low=level - 1, close=level)
            )

        level = Decimal(1000)

        for _ in range(300):
            level_bar(level)

        for _ in range(4):
            for _ in range(rise):
                level += step
                level_bar(level)
            for _ in range(pullback):
                level -= step
                level_bar(level)

        protected_low = level - 2

        for _ in range(failure_rally):
            level += step
            level_bar(level)

        pre_mss_extreme = level + 2

        for _ in range(5):
            level -= 1
            level_bar(level)

        choch_close = protected_low - drop
        choch_low = choch_close - 1
        candles.append(
            bar(len(candles), open_=level, high=level + 1, low=choch_low, close=choch_close)
        )

        for _ in range(followthrough_after - 1):
            candles.append(
                bar(
                    len(candles),
                    open_=choch_close,
                    high=choch_close + 1,
                    low=choch_low + Decimal("0.5"),
                    close=choch_close,
                )
            )

        base = choch_low - 3
        candles.append(
            bar(len(candles), open_=choch_close, high=choch_close, low=base - 1, close=base)
        )

        for since in range(1, 15):
            if since == reclaim_delay:
                target = pre_mss_extreme + overshoot
                candles.append(
                    bar(len(candles), open_=base, high=target + 1, low=base - 1, close=target)
                )
                base = target
            else:
                flat_bar(base)

        for _ in range(8):
            flat_bar(base)

        return candles, reclaim_delay

    if reclaim_delays is None:
        # Leaning on the edges, see the docstring; one draw in four never
        # comes back.
        reclaim_delays = st.tuples(
            st.sampled_from([True, True, True, False]),
            st.one_of(st.sampled_from([1, 10, 11]), st.integers(min_value=1, max_value=14)),
        ).map(lambda drawn: drawn[1] if drawn[0] else None)

    return st.tuples(
        st.integers(min_value=9, max_value=12),
        st.integers(min_value=5, max_value=6),
        st.integers(min_value=3, max_value=8),
        st.integers(min_value=1, max_value=3),
        reclaim_delays,
        st.integers(min_value=1, max_value=5),
    ).map(build)


def walking_candle_series(
    *,
    min_size: int = 20,
    max_size: int = 120,
    timeframe: Timeframe = Timeframe.H1,
    symbol: str = "PROPWALK",
) -> st.SearchStrategy[list[Candle]]:
    """A price series whose level *walks* instead of teleporting.

    `candle_series` draws each midpoint independently over a wide band, which
    is ideal for structure -- §3.1 reads highs and lows and does not care how
    price got there. It is useless for anything denominated in ATR. A jump
    between two independent midpoints becomes the candle's true range through
    the gap terms, so ATR runs an order of magnitude above any single candle's
    own range, and every `range >= n x ATR` test fails by construction.

    Measured: of 200 series from `candle_series`, **zero** contained a single
    §5.10 displacement. A property over that strategy is not lenient, it is
    vacuous -- it passes by finding nothing, which is the failure mode this
    project keeps naming.

    So this walks: each candle steps a small amount from the last close, and
    a drawn subset of candles carries a wide range and a close pinned near one
    extreme -- the shape §5.10 calls displacement. The impulses are drawn, not
    planted at fixed offsets, so hypothesis still explores where they land and
    how large they are.
    """

    def build(rows: list[tuple[int, int, int, int, bool]]) -> list[Candle]:
        candles: list[Candle] = []
        level = 1000

        for index, (step, spread, body_lo, body_hi, impulse) in enumerate(rows):
            level = max(200, min(2000, level + step))

            half = spread * 8 if impulse else spread
            high = level + half
            low = level - half

            if impulse and half > 0:
                # Close inside the top or bottom eighth: §5.10 wants the close
                # in the extreme 25% of the range, and a full body behind it.
                if body_lo % 2:
                    open_price, close_price = low, high - half // 8
                else:
                    open_price, close_price = high, low + half // 8
            elif half > 0:
                span = 2 * half
                open_price = low + (span * (body_lo % 101)) // 100
                close_price = low + (span * (body_hi % 101)) // 100
            else:
                open_price = close_price = level

            candles.append(
                Candle(
                    symbol=symbol,
                    timeframe=timeframe,
                    open_time=_SERIES_ORIGIN + timeframe.duration * index,
                    open=Decimal(open_price),
                    high=Decimal(high),
                    low=Decimal(low),
                    close=Decimal(close_price),
                    volume=Decimal(100),
                    quote_volume=Decimal(10_000),
                    taker_buy_volume=Decimal(50),
                    trade_count=10,
                    source=CandleSource.BACKFILL,
                )
            )

            level = close_price

        return candles

    return st.lists(
        st.tuples(
            st.integers(min_value=-6, max_value=6),
            st.integers(min_value=1, max_value=6),
            st.integers(min_value=0, max_value=100),
            st.integers(min_value=0, max_value=100),
            st.booleans(),
        ),
        min_size=min_size,
        max_size=max_size,
    ).map(build)


def candle_series(
    *,
    min_size: int = 1,
    max_size: int = 80,
    timeframe: Timeframe = Timeframe.H1,
    symbol: str = "PROPUSDT",
    with_bodies: bool = False,
) -> st.SearchStrategy[list[Candle]]:
    """Contiguous, always-valid candle series for property tests.

    Each candle is generated from a midpoint and a half-range. That
    construction satisfies the §2.15 intrinsic checks by shape rather than by
    filtering, so hypothesis spends its budget exploring price *structure*
    instead of rediscovering that `high >= max(open, close)`.

    `with_bodies` opens and closes the candle inside its own range instead of
    pinning both to the midpoint. The default stays body-less because that is
    what the swing properties want -- §3.1 reads highs and lows and nothing
    else. §5.10 does read the body, and `body == 0` makes every candle fail
    its first condition, so a displacement property over the default strategy
    would pass without ever detecting one.
    """

    def build(rows: list[tuple[int, int, int, int]]) -> list[Candle]:
        candles = []

        for index, (mid, half, open_offset, close_offset) in enumerate(rows):
            high = mid + half
            low = mid - half

            if with_bodies and half > 0:
                # Offsets are drawn 0..100 and scaled into the candle's own
                # range, so the OHLC ordering holds by construction for any
                # half-range hypothesis picks.
                open_price = low + (2 * half * open_offset) // 100
                close_price = low + (2 * half * close_offset) // 100
            else:
                open_price = close_price = mid

            candles.append(
                Candle(
                    symbol=symbol,
                    timeframe=timeframe,
                    open_time=_SERIES_ORIGIN + timeframe.duration * index,
                    open=Decimal(open_price),
                    high=Decimal(high),
                    low=Decimal(low),
                    close=Decimal(close_price),
                    volume=Decimal(100),
                    quote_volume=Decimal(10_000),
                    taker_buy_volume=Decimal(50),
                    trade_count=10,
                    source=CandleSource.BACKFILL,
                )
            )

        return candles

    return st.lists(
        st.tuples(
            st.integers(min_value=50, max_value=500),
            st.integers(min_value=0, max_value=20),
            st.integers(min_value=0, max_value=100),
            st.integers(min_value=0, max_value=100),
        ),
        min_size=min_size,
        max_size=max_size,
    ).map(build)
