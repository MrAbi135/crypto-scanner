"""Unit tests for Sprint S3 worker scheduling helpers."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest

from scanner.runtime import worker


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
    symbols.list_active = AsyncMock(side_effect=asyncio.CancelledError)

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
        )

    sleep_mock.assert_awaited_once_with(123.0)


@pytest.mark.asyncio
async def test_daily_loop_runs_job_for_each_active_symbol() -> None:
    class Symbol:
        def __init__(
            self,
            exchange_symbol: str,
        ) -> None:
            self.exchange_symbol = exchange_symbol

    report = AsyncMock()
    report.evaluation = None

    job = AsyncMock()
    job.run_symbol = AsyncMock(return_value=report)

    symbols = AsyncMock()
    symbols.list_active = AsyncMock(
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
        )

    assert job.run_symbol.await_count == 2

    job.run_symbol.assert_any_await("BTCUSDT")
    job.run_symbol.assert_any_await("ETHUSDT")
