"""Request-scoped dependencies for the read API (Sprint S10a)."""

from __future__ import annotations

from fastapi import Request

from scanner.application.identity import AccountService, SessionService
from scanner.application.identity.tokens import AccessTokens
from scanner.application.ports import CandleRepository, Clock
from scanner.application.ports.ict_evidence import IctEvidenceRepository
from scanner.application.ports.ict_zones import IctZoneRepository
from scanner.application.ports.liquidity_detection import LiquidityPoolRepository
from scanner.application.ports.sessions import SessionRepository
from scanner.application.ports.signal_outcomes import SignalOutcomeRepository
from scanner.application.ports.signal_transitions import SignalTransitionRepository
from scanner.application.ports.signals import SignalRepository
from scanner.application.ports.track_record import TrackRecordRepository
from scanner.interfaces.api.query import CursorCodec


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


def get_accounts(request: Request) -> AccountService:
    return request.app.state.accounts  # type: ignore[no-any-return]


def get_sessions(request: Request) -> SessionService:
    return request.app.state.sessions  # type: ignore[no-any-return]


def get_session_repository(request: Request) -> SessionRepository:
    """The repository, not the service.

    The session-list and revoke rows read and write T22 directly: they are
    not rotating anything, and routing them through `SessionService` would
    mean adding methods there that only an endpoint uses.
    """
    return request.app.state.session_repository  # type: ignore[no-any-return]


def get_access_tokens(request: Request) -> AccessTokens:
    return request.app.state.access_tokens  # type: ignore[no-any-return]


def get_signals(request: Request) -> SignalRepository:
    return request.app.state.signals  # type: ignore[no-any-return]


def get_signal_transitions(request: Request) -> SignalTransitionRepository:
    return request.app.state.signal_transitions  # type: ignore[no-any-return]


def get_outcomes(request: Request) -> SignalOutcomeRepository:
    return request.app.state.outcomes  # type: ignore[no-any-return]


def get_track_record(request: Request) -> TrackRecordRepository:
    return request.app.state.track_record  # type: ignore[no-any-return]


def get_cursors(request: Request) -> CursorCodec:
    return request.app.state.cursors  # type: ignore[no-any-return]
