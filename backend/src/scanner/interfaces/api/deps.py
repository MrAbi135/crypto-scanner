"""Request-scoped dependencies for the read API (Sprint S10a)."""

from __future__ import annotations

from fastapi import Request

from scanner.application.ports import CandleRepository, Clock
from scanner.application.ports.ict_evidence import IctEvidenceRepository
from scanner.application.ports.ict_zones import IctZoneRepository
from scanner.application.ports.liquidity_detection import LiquidityPoolRepository


def get_candles(request: Request) -> CandleRepository:
    return request.app.state.candles  # type: ignore[no-any-return]


def get_clock(request: Request) -> Clock:
    return request.app.state.clock  # type: ignore[no-any-return]


def get_evidence(request: Request) -> IctEvidenceRepository:
    return request.app.state.evidence  # type: ignore[no-any-return]


def get_zones(request: Request) -> IctZoneRepository:
    return request.app.state.zones  # type: ignore[no-any-return]


def get_pools(request: Request) -> LiquidityPoolRepository:
    return request.app.state.pools  # type: ignore[no-any-return]
