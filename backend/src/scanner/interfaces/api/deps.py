"""Request-scoped dependencies for the read API (Sprint S10a)."""

from __future__ import annotations

from fastapi import Request

from scanner.application.ports import CandleRepository, Clock


def get_candles(request: Request) -> CandleRepository:
    return request.app.state.candles  # type: ignore[no-any-return]


def get_clock(request: Request) -> Clock:
    return request.app.state.clock  # type: ignore[no-any-return]
