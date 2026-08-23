"""Unit tests for Sprint S3 worker scheduling helpers."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest

from scanner.runtime import worker
from tests.support.clock import FakeClock


class Symbol:
    """Only the one attribute the loop reads."""

    def __init__(self, exchange_symbol: str) -> None:
        self.exchange_symbol = exchange_symbol


@pytest.mark.asyncio
async def test_seconds_until_next_utc_midnight() -> None:
    fixed_now = datetime(
        2026,
        8,
        10,
        12,
        30,
        tzinfo=UTC,
    )

    class FakeDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed_now

    with patch.object(
        worker,
        "datetime",
        FakeDateTime,
    ):
        seconds = await worker._seconds_until_next_utc_midnight()

    assert seconds == 41400.0


@pytest.mark.asyncio
async def test_daily_loop_waits_until_midnight_before_loading_symbols() -> None:
    job = AsyncMock()

    symbols = AsyncMock()
    symbols.list_observable = AsyncMock(side_effect=asyncio.CancelledError)

    with (
        patch.object(
            worker,
            "_seconds_until_next_utc_midnight",
            AsyncMock(return_value=123.0),
        ),
        patch.object(
            worker.asyncio,
            "sleep",
            AsyncMock(),
        ) as sleep_mock,
        pytest.raises(asyncio.CancelledError),
    ):
        await worker._run_daily_universe_loop(
            job,
            symbols,
            AsyncMock(),
            AsyncMock(),
            FakeClock(),
        )

    sleep_mock.assert_awaited_once_with(123.0)


@pytest.mark.asyncio
async def test_daily_loop_runs_job_for_each_active_symbol() -> None:
    report = AsyncMock()
    report.evaluation = None

    job = AsyncMock()
    job.run_symbol = AsyncMock(return_value=report)

    symbols = AsyncMock()
    symbols.list_observable = AsyncMock(
        side_effect=[
            [
                Symbol("BTCUSDT"),
                Symbol("ETHUSDT"),
            ],
            asyncio.CancelledError,
        ]
    )

    with (
        patch.object(
            worker,
            "_seconds_until_next_utc_midnight",
            AsyncMock(return_value=0.0),
        ),
        patch.object(
            worker.asyncio,
            "sleep",
            AsyncMock(),
        ),
        pytest.raises(asyncio.CancelledError),
    ):
        await worker._run_daily_universe_loop(
            job,
            symbols,
            AsyncMock(),
            AsyncMock(),
            FakeClock(),
        )

    assert job.run_symbol.await_count == 2

    job.run_symbol.assert_any_await("BTCUSDT")
    job.run_symbol.assert_any_await("ETHUSDT")


@pytest.mark.asyncio
async def test_the_registry_is_synced_at_boot_before_the_first_sleep() -> None:
    """`market.symbols` held zero rows for the project's whole life.

    `sync-symbols` existed only as a CLI command nobody had cause to type, and
    every loop here iterates the registry -- so an empty one made the
    worker a no-op that looked perfectly healthy. Ordering matters: syncing
    after the sleep would leave the first day unusable.
    """
    sync = AsyncMock()
    symbols = AsyncMock()
    symbols.list_observable = AsyncMock(side_effect=asyncio.CancelledError)

    with (
        patch.object(
            worker,
            "_seconds_until_next_utc_midnight",
            AsyncMock(return_value=1.0),
        ),
        patch.object(worker.asyncio, "sleep", AsyncMock()) as sleep_mock,
        pytest.raises(asyncio.CancelledError),
    ):
        await worker._run_daily_universe_loop(AsyncMock(), symbols, sync, AsyncMock(), FakeClock())

    # Twice: once at boot, once after the first sleep, before evaluation.
    assert sync.sync.await_count == 2
    sleep_mock.assert_awaited_once_with(1.0)


@pytest.mark.asyncio
async def test_an_unreachable_venue_does_not_take_the_worker_down() -> None:
    """The registry we already hold stays usable; the next attempt is a day away."""
    sync = AsyncMock()
    sync.sync = AsyncMock(side_effect=ConnectionError("binance unreachable"))

    await worker._sync_symbols(sync)


@pytest.mark.asyncio
async def test_the_loop_measures_quarantined_symbols_not_just_active_ones() -> None:
    """§1.4 promotes on seven consecutive daily evaluations.

    A symbol has to be measured to accumulate them, so iterating `list_active()`
    could never promote anything: on the VM that meant 484 QUARANTINE, 249
    DELISTED, zero ACTIVE, and a nightly loop with an empty list to walk.
    """
    symbols = AsyncMock()
    symbols.list_active = AsyncMock(side_effect=AssertionError("must not narrow to ACTIVE"))
    symbols.list_observable = AsyncMock(side_effect=asyncio.CancelledError)

    with (
        patch.object(
            worker,
            "_seconds_until_next_utc_midnight",
            AsyncMock(return_value=1.0),
        ),
        patch.object(worker.asyncio, "sleep", AsyncMock()),
        pytest.raises(asyncio.CancelledError),
    ):
        await worker._run_daily_universe_loop(
            AsyncMock(), symbols, AsyncMock(), AsyncMock(), FakeClock()
        )

    symbols.list_observable.assert_awaited()
    symbols.list_active.assert_not_awaited()


@pytest.mark.asyncio
async def test_the_daily_loop_also_runs_the_fake_volume_evaluation() -> None:
    """§6.6 is "recomputed daily", and until now nothing called it.

    The job existed, was tested, and had no caller -- the exact condition the
    unwired-detector review was opened for.
    """
    symbols = AsyncMock()
    symbols.list_observable = AsyncMock(side_effect=[[Symbol("BTCUSDT")], asyncio.CancelledError])

    fake_volume = AsyncMock()

    with (
        patch.object(
            worker,
            "_seconds_until_next_utc_midnight",
            AsyncMock(return_value=1.0),
        ),
        patch.object(worker.asyncio, "sleep", AsyncMock()),
        pytest.raises(asyncio.CancelledError),
    ):
        await worker._run_daily_universe_loop(
            AsyncMock(),
            symbols,
            AsyncMock(),
            fake_volume,
            FakeClock(datetime(2026, 8, 23, 0, 0, tzinfo=UTC)),
        )

    fake_volume.run_symbol.assert_awaited_once()

    symbol, day = fake_volume.run_symbol.await_args.args

    assert symbol == "BTCUSDT"

    # The closed day, not today: the loop wakes at midnight and today has no
    # candles yet.
    assert day == datetime(2026, 8, 22, tzinfo=UTC)


@pytest.mark.asyncio
async def test_a_tiering_failure_does_not_take_the_integrity_check_with_it() -> None:
    """§6.6 scores a symbol whether or not §1.4 could evaluate it."""
    job = AsyncMock()
    job.run_symbol = AsyncMock(side_effect=RuntimeError("binance down"))

    symbols = AsyncMock()
    symbols.list_observable = AsyncMock(side_effect=[[Symbol("BTCUSDT")], asyncio.CancelledError])

    fake_volume = AsyncMock()

    with (
        patch.object(
            worker,
            "_seconds_until_next_utc_midnight",
            AsyncMock(return_value=1.0),
        ),
        patch.object(worker.asyncio, "sleep", AsyncMock()),
        pytest.raises(asyncio.CancelledError),
    ):
        await worker._run_daily_universe_loop(
            job,
            symbols,
            AsyncMock(),
            fake_volume,
            FakeClock(datetime(2026, 8, 23, tzinfo=UTC)),
        )

    fake_volume.run_symbol.assert_awaited_once()
