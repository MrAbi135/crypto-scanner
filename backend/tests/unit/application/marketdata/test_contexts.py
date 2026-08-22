"""The ingested context set, and the ladder rule (Sprint S3b)."""

from __future__ import annotations

import pytest

from scanner.application.marketdata.contexts import (
    parse_symbols,
    parse_timeframes,
    stream_names,
)
from scanner.shared import Timeframe
from scanner.shared.errors import ValidationError


def test_symbols_are_normalised_and_trimmed() -> None:
    assert parse_symbols(" btcusdt , ETHUSDT ") == ("BTCUSDT", "ETHUSDT")


@pytest.mark.parametrize("raw", ["", "   ", ",,"])
def test_an_empty_symbol_list_is_refused(raw: str) -> None:
    with pytest.raises(ValidationError, match="at least one ingest symbol"):
        parse_symbols(raw)


def test_duplicate_symbols_are_refused() -> None:
    """Two subscriptions to one stream double every insert attempt for nothing."""
    with pytest.raises(ValidationError, match="duplicate"):
        parse_symbols("BTCUSDT,btcusdt")


def test_timeframes_come_back_in_ladder_order_regardless_of_input() -> None:
    """Warm-up runs bottom-up, so the order must not depend on how it was typed."""
    assert parse_timeframes("H1,M5,M15") == (
        Timeframe.M5,
        Timeframe.M15,
        Timeframe.H1,
    )


def test_a_hole_in_the_ladder_is_refused_at_boot() -> None:
    """The finding this whole rule exists for.

    Running the engine on real BTC H1 with no M15 ingested gave 3581 touches,
    476 rejections and **zero** confirmations -- because a higher timeframe
    reads the one below it to find the LTF break that confirms a reaction, and
    an empty series there means the branch never runs. Nothing fails. The count
    is simply always 0, forever.

    Backfilling M15 turned it into 235. Refusing the config at boot is cheaper
    than rediscovering that from a suspicious dashboard weeks later.
    """
    with pytest.raises(ValidationError, match="hole in the ladder"):
        parse_timeframes("M5,H1")

    with pytest.raises(ValidationError, match="M15, H1"):
        parse_timeframes("M5,H4")


def test_a_ladder_that_starts_high_is_fine_if_it_is_contiguous() -> None:
    """Not every deployment scans from M5 up.

    Starting at H1 means H1 has no ingested LTF and will not confirm -- a real
    cost, but a chosen one. The rule catches holes, not the floor.
    """
    assert parse_timeframes("H1,H4,D1") == (
        Timeframe.H1,
        Timeframe.H4,
        Timeframe.D1,
    )


def test_a_single_timeframe_is_contiguous_by_definition() -> None:
    assert parse_timeframes("M5") == (Timeframe.M5,)


def test_an_unknown_timeframe_is_refused() -> None:
    with pytest.raises(ValidationError):
        parse_timeframes("M5,M3")


def test_stream_names_pair_every_symbol_with_every_timeframe() -> None:
    names = stream_names(("BTCUSDT", "ETHUSDT"), (Timeframe.M5, Timeframe.H1))

    assert names == (
        "BTCUSDT@kline_5m",
        "BTCUSDT@kline_1h",
        "ETHUSDT@kline_5m",
        "ETHUSDT@kline_1h",
    )


def test_every_scanned_timeframe_has_a_binance_interval() -> None:
    """A missing entry would be a KeyError at boot, on a Friday, in production."""
    names = stream_names(("BTCUSDT",), tuple(Timeframe))

    assert len(names) == len(Timeframe)


def test_trade_streams_are_added_per_symbol_not_per_context() -> None:
    """The tape belongs to the symbol. Subscribing per timeframe would deliver
    every print once for each one."""
    streams = stream_names(
        ("BTCUSDT", "ETHUSDT"),
        (Timeframe.M5, Timeframe.H1),
        trades=True,
    )

    assert streams.count("btcusdt@aggTrade") + streams.count("BTCUSDT@aggTrade") == 1
    assert len([s for s in streams if "aggTrade" in s]) == 2
    assert len([s for s in streams if "kline" in s]) == 4


def test_trade_streams_are_off_unless_asked_for() -> None:
    """The aggTrade stream is the highest-volume subscription Binance offers,
    and §6.5 declares itself unread without it rather than breaking."""
    streams = stream_names(("BTCUSDT",), (Timeframe.M5,))

    assert not [s for s in streams if "aggTrade" in s]
