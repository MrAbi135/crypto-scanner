"""Score the published signals in R, and test alternative stop/target/TTL geometry.

**Why this exists.** On 2026-10-10 the 37 published signals stood at 4 SUCCESS
against 27 losses, and the question was whether the bracket around the setups
-- stop distance, target distance, TTL -- was the cause. Answering it needs the
trades replayed under other geometry, and the first attempt at that analysis
nearly reported the wrong number twice. Both traps are built into this file so
a later run cannot fall into them again:

* **Hit rate is not the answer, so it is never reported alone.** A nearer
  target raises the hit rate while shrinking every win; a wider stop raises it
  while enlarging every loss. One configuration measured a **54% hit rate that
  still lost 0.243R a trade**. Expectancy in R is the answer; the hit rate is
  printed beside it and labelled as context.
* **A positive result is not an edge until its concentration is known.** The
  one profitable configuration found on 37 trades was **108% a single trade**
  and inverted when that trade was dropped. `concentration()` runs that test on
  whichever configuration looks best, automatically, so nobody has to remember
  to ask.

**R is always the signal's ORIGINAL stop distance**, never the stop under test.
Mixing units across configurations is how a wider stop can be made to look
profitable: its losses shrink in its own units and in nothing else.

**The control is what makes the rest trustworthy.** Before printing any
counterfactual, the live geometry is scored and compared against the engine's
own labels, per signal. The engine's semantics are asymmetric -- SUCCESS is the
target being *touched*, FAILED is the invalidation being *closed through* -- so
the close convention is the control, and the touch convention is reported
beside it because the repo's own CLAUDE.md records the same signals at PF 1.121
close-based and PF 0.697 touch-based. If agreement falls below `CONTROL_FLOOR`
this exits non-zero and prints every disagreement, because a drifted simulator
that still produces plausible numbers is worse than no simulator.

**What it does not model:** fees, slippage and spread, none of them. Every
figure is gross, and costs move all of them the same way. And when one candle
contains both the target and the stop, the order is unknowable from OHLC, so
the trade is scored a LOSS -- stated because it flatters nothing. The candle
that touches the entry only activates the signal, as the engine's `observe()`
does; stop and target are judged from the following candle.

Read-only. Runs from the engine image for its database access, exactly as
`break_tolerance.py` does; `ops/soak/run_signal_outcomes.sh` wraps the
invocation.
"""

from __future__ import annotations

import asyncio
import os
import sys
from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

# The longest TTL any configuration below asks for. The candle fetch is sized
# from it, so a longer sweep cannot silently run out of candles and read the
# shortfall as "expired" -- which would make every long-TTL row look better
# than it is.
MAX_TTL = 96

TF_SECONDS = {"M5": 300, "M15": 900, "H1": 3_600, "H4": 14_400, "D1": 86_400, "W1": 604_800}

# Per-signal agreement between this simulator and the engine's own labels,
# below which the counterfactuals are not reported at all. Measured at 36/37 on
# 2026-10-10; 0.85 leaves room for the boundary candles OHLC cannot order
# without tolerating a simulator that has quietly stopped matching.
CONTROL_FLOOR = Decimal("0.85")

TERMINAL = (
    "SUCCESS",
    "FAILED",
    "EXPIRED_ACTIVE",
    "EXPIRED_UNTOUCHED",
    "INVALIDATED_EARLY",
)

# The three labels that represent a position that was actually opened. The two
# zero-R labels are counted but carry no P&L, so averaging over them would pull
# every expectancy toward zero by however many of them a configuration makes.
FILLED = frozenset({"SUCCESS", "FAILED", "EXPIRED_ACTIVE"})


@dataclass(frozen=True, slots=True)
class Signal:
    signal_id: str
    symbol: str
    timeframe: str
    direction: str
    grade: str
    entry: Decimal
    invalidation: Decimal
    target: Decimal
    ttl_candles: int
    engine_label: str


@dataclass(frozen=True, slots=True)
class Bar:
    high: Decimal
    low: Decimal
    close: Decimal


@dataclass(frozen=True, slots=True)
class Outcome:
    label: str
    r: Decimal
    """Signed result in units of the signal's ORIGINAL stop distance."""


def score(
    signal: Signal,
    bars: list[Bar],
    *,
    stop_mult: Decimal = Decimal(1),
    target_r: Decimal | None = None,
    ttl: int = 24,
    close_stop: bool = True,
) -> Outcome | None:
    """One signal under one geometry. `target_r=None` keeps the published target.

    Returns None when the signal cannot be scored at all -- a zero stop
    distance, or no forward candles -- rather than inventing a flat result,
    which would dilute every average by however many such rows exist.
    """
    base = abs(signal.entry - signal.invalidation)

    if base == 0 or not bars:
        return None

    up = signal.direction == "UP"
    stop = signal.entry - base * stop_mult if up else signal.entry + base * stop_mult

    if target_r is None:
        target = signal.target
    else:
        target = signal.entry + base * target_r if up else signal.entry - base * target_r

    target_in_r = abs(target - signal.entry) / base
    live = False

    for bar in bars[:ttl]:
        if not live:
            # §12.3: nothing can happen to a signal whose entry was never
            # touched. Dropping this requirement turns every invalidation into
            # a loss on a position that was never opened.
            if (up and bar.low <= signal.entry) or (not up and bar.high >= signal.entry):
                live = True
                # The entry candle only activates; stop and target are read
                # from the NEXT candle on. That is the engine's convention --
                # `lifecycle/state.py::observe()` returns ACTIVE as soon as the
                # entry is touched, without looking at the invalidation or the
                # target on that candle -- and the control is only a control
                # if both sides judge the same candles. It is not a detail: a
                # tight zone-edge stop is often closed through on the entry
                # candle itself, and judging that candle turned M5's
                # zone-edge stop from +0.71% into -11.29% (n=15; SLS erratum
                # PR #312).
                continue
            else:
                # There is deliberately no "invalidated before entry" branch
                # here, and the reason is arithmetic rather than taste: a first
                # draft had one and it could never fire. For a long the stop
                # sits BELOW the entry, so "entry not touched" means
                # `low > entry`, and then `close >= low > entry > stop` -- the
                # stop cannot have been broken. The mirror holds for a short.
                # Under an OHLC fill model price cannot reach the invalidation
                # without passing through the entry first.
                #
                # So the engine's own INVALIDATED_EARLY signals (3 of 37 on
                # 2026-10-10) are scored here as whatever their candles say,
                # usually FAILED, and the control gate prints them as
                # disagreements. That is the honest place for them: visible in
                # the control, rather than silently zeroed by a branch that
                # looks like it handles the case and never runs.
                continue

        hit_stop = (
            (bar.close <= stop if up else bar.close >= stop)
            if close_stop
            else (bar.low <= stop if up else bar.high >= stop)
        )
        hit_target = bar.high >= target if up else bar.low <= target

        # Stop first: with both inside one candle the order is unknowable from
        # OHLC, and scoring it a win would flatter exactly the configurations
        # that widen the stop until both fit in the same bar.
        if hit_stop:
            return Outcome("FAILED", -stop_mult)

        if hit_target:
            return Outcome("SUCCESS", target_in_r)

    if not live:
        return Outcome("EXPIRED_UNTOUCHED", Decimal(0))

    # Marked to the last close rather than dropped. Dropping unresolved trades
    # would silently favour the configurations that leave most of them open --
    # which is what a wider stop and a longer TTL both do.
    last = bars[min(ttl, len(bars)) - 1].close
    held = (last - signal.entry) if up else (signal.entry - last)

    return Outcome("EXPIRED_ACTIVE", held / base)


@dataclass(frozen=True, slots=True)
class Tally:
    counts: dict[str, int]
    total_r: Decimal
    per_trade: Decimal
    wins: int
    losses: int
    scored: int

    @property
    def hit_rate(self) -> Decimal | None:
        """Context only. `per_trade` is the answer -- see this module's docstring."""
        decided = self.wins + self.losses

        return Decimal(100) * self.wins / decided if decided else None


def tally(signals: list[Signal], bars: dict[str, list[Bar]], **geometry) -> Tally:
    counts: dict[str, int] = defaultdict(int)
    total = Decimal(0)
    scored = wins = losses = 0

    for signal in signals:
        outcome = score(signal, bars.get(signal.signal_id, []), **geometry)

        if outcome is None:
            continue

        counts[outcome.label] += 1

        if outcome.label in FILLED:
            total += outcome.r
            scored += 1
            wins += 1 if outcome.r > 0 else 0
            losses += 1 if outcome.r < 0 else 0

    return Tally(
        counts=dict(counts),
        total_r=total,
        per_trade=total / scored if scored else Decimal(0),
        wins=wins,
        losses=losses,
        scored=scored,
    )


def concentration(
    signals: list[Signal],
    bars: dict[str, list[Bar]],
    **geometry,
) -> list[tuple[int, Decimal, Decimal, int]]:
    """Per-trade R after dropping the best 0, 1, 2 and 3 trades.

    A result that inverts when its best trade is removed is one trade, not an
    edge. This runs on the best configuration automatically, because the
    2026-10-10 analysis found a +0.275R configuration whose best single trade
    was 108% of its total.
    """
    results: list[Decimal] = []

    for signal in signals:
        outcome = score(signal, bars.get(signal.signal_id, []), **geometry)

        if outcome is not None and outcome.label in FILLED:
            results.append(outcome.r)

    results.sort(reverse=True)
    rows: list[tuple[int, Decimal, Decimal, int]] = []

    for dropped in range(4):
        kept = results[dropped:]

        if not kept:
            break

        subtotal = sum(kept, Decimal(0))
        rows.append((dropped, subtotal, subtotal / len(kept), len(kept)))

    return rows


# --------------------------------------------------------------------------
# Loading
# --------------------------------------------------------------------------


def _dsn() -> str:
    raw = os.environ.get("SCANNER_DB_DSN") or os.environ["DATABASE_URL"]

    return raw.replace("postgresql://", "postgresql+asyncpg://", 1)


async def _signals(conn) -> list[Signal]:
    rows = await conn.execute(
        text(
            """
            select g.signal_id, g.symbol, g.timeframe, g.direction, g.grade,
                   g.entry_proximal, g.invalidation_level,
                   ((g.target_bands::jsonb)->'primary'->>'high')::numeric,
                   g.ttl_candles,
                   coalesce((select t.to_state from detection.signal_transitions t
                              where t.signal_id = g.signal_id
                                and t.to_state = any(:terminal)
                              order by t.at_candle_open_time desc limit 1), 'PENDING')
              from detection.signals g
             where (g.target_bands::jsonb)->'primary'->>'high' is not null
             order by g.published_at
            """
        ),
        {"terminal": list(TERMINAL)},
    )

    return [
        Signal(
            signal_id=row[0],
            symbol=row[1],
            timeframe=row[2],
            direction=row[3],
            grade=row[4],
            entry=row[5],
            invalidation=row[6],
            target=row[7],
            ttl_candles=row[8],
            engine_label=row[9],
        )
        for row in rows
    ]


async def _bars(conn) -> dict[str, list[Bar]]:
    """Forward candles per signal, strictly AFTER the published candle.

    `published_at` is the candle's OPEN time -- `confluence_replay.py` writes
    `published_at=event_at` and §12.3 makes a signal eligible from the next
    candle -- so a window starting at `published_at` itself would let a signal
    resolve on the very bar that created it.
    """
    rows = await conn.execute(
        text(
            """
            select g.signal_id, c.high, c.low, c.close
              from detection.signals g
              join market.candles c
                on c.symbol = g.symbol
               and c.timeframe = g.timeframe
               and c.open_time > g.published_at
               and c.open_time <= g.published_at
                                  + make_interval(secs => :max_ttl * (
                                      case g.timeframe
                                        when 'M5' then 300 when 'M15' then 900
                                        when 'H1' then 3600 when 'H4' then 14400
                                        when 'D1' then 86400 else 604800 end))
             order by g.signal_id, c.open_time
            """
        ),
        {"max_ttl": MAX_TTL},
    )

    out: dict[str, list[Bar]] = defaultdict(list)

    for signal_id, high, low, close in rows:
        out[signal_id].append(Bar(high=high, low=low, close=close))

    return dict(out)


# --------------------------------------------------------------------------
# Reporting
# --------------------------------------------------------------------------


def _line(label: str, result: Tally) -> str:
    hit = f"{result.hit_rate:.0f}%" if result.hit_rate is not None else "-"

    return (
        f"  {label:<34} per-trade={float(result.per_trade):+6.3f}R  "
        f"total={float(result.total_r):+7.2f}R  (hit {hit:>4}, context only)  n={result.scored}"
    )


def control_passes(signals: list[Signal], bars: dict[str, list[Bar]]) -> bool:
    """Reproduce the engine's own labels, or refuse to report anything else."""
    checked = agreed = 0
    disagreements: list[str] = []

    for signal in signals:
        if signal.engine_label == "PENDING":
            continue

        outcome = score(signal, bars.get(signal.signal_id, []), ttl=signal.ttl_candles)

        if outcome is None:
            continue

        checked += 1

        if outcome.label == signal.engine_label:
            agreed += 1
        else:
            disagreements.append(
                f"    {signal.signal_id[:12]} {signal.symbol:<10} {signal.timeframe:<4} "
                f"engine={signal.engine_label:<18} simulated={outcome.label}"
            )

    if not checked:
        print(
            "CONTROL: no resolved signal to check against -- refusing to report",
            file=sys.stderr,
        )

        return False

    rate = Decimal(agreed) / Decimal(checked)

    print(f"CONTROL: {agreed}/{checked} labels reproduced ({float(100 * rate):.0f}%)")

    if disagreements:
        print("  disagreements (expected on candles OHLC cannot order):")
        print("\n".join(disagreements))

    if rate < CONTROL_FLOOR:
        print(
            f"\nCONTROL FAILED: {float(100 * rate):.0f}% is under the "
            f"{float(100 * CONTROL_FLOOR):.0f}% floor. The simulator and the engine disagree "
            "about what an outcome is, so nothing below it would mean anything. Fix the "
            "simulator -- or, if §12's semantics changed, this file.",
            file=sys.stderr,
        )

        return False

    return True


def sweeps() -> list[tuple[str, dict]]:
    """The three levers the 2026-10-10 analysis was asked about, and their pairs."""
    out: list[tuple[str, dict]] = []

    for mult in (2, 3):
        out.append((f"stop x{mult}", {"stop_mult": Decimal(mult), "ttl": 24}))

    for ttl in (48, 96):
        out.append((f"TTL {ttl}", {"ttl": ttl}))

    for target in (Decimal(1), Decimal("1.5"), Decimal(2), Decimal(3)):
        out.append((f"target {target}R", {"target_r": target, "ttl": 24}))

    for mult in (2, 3):
        for target in (Decimal("1.5"), Decimal(2), None):
            name = "published target" if target is None else f"target {target}R"
            out.append(
                (
                    f"stop x{mult}, {name}, TTL 48",
                    {"stop_mult": Decimal(mult), "target_r": target, "ttl": 48},
                )
            )

    return out


def report(signals: list[Signal], bars: dict[str, list[Bar]]) -> int:
    total_bars = sum(len(b) for b in bars.values())

    print(f"{len(signals)} published signals, {total_bars} forward candles")
    print("R = the signal's ORIGINAL stop distance. Gross: no fees, slippage or spread.\n")

    if not control_passes(signals, bars):
        return 1

    print("\nLIVE GEOMETRY (stop x1, published target, TTL 24)")
    print(_line("close-through stop (§12's rule)", tally(signals, bars, ttl=24)))
    print(_line("touch stop", tally(signals, bars, ttl=24, close_stop=False)))

    print("\nALTERNATIVES")

    scored = [(name, tally(signals, bars, **geometry), geometry) for name, geometry in sweeps()]

    for name, result, _ in scored:
        print(_line(name, result))

    best_name, best, best_geometry = max(scored, key=lambda row: row[1].per_trade)

    print(f"\nCONCENTRATION of the best alternative -- {best_name}")
    print("  A result that inverts without its best trade is one trade, not an edge.")

    for dropped, subtotal, per_trade, kept in concentration(signals, bars, **best_geometry):
        tag = "   <= as measured" if dropped == 0 else ""
        print(
            f"    drop best {dropped}: total={float(subtotal):+7.2f}R  "
            f"per-trade={float(per_trade):+6.3f}R  (n={kept}){tag}"
        )

    if best.per_trade <= 0:
        print("\n  No alternative is positive. Nothing here argues for a parameter change.")

    return 0


async def main() -> int:
    engine = create_async_engine(_dsn())

    try:
        async with engine.connect() as conn:
            signals = await _signals(conn)
            bars = await _bars(conn)
    finally:
        await engine.dispose()

    if not signals:
        print("no published signal carries a target band -- nothing to score", file=sys.stderr)

        return 1

    return report(signals, bars)


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
