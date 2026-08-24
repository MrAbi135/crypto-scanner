"""Signal lifecycle (SLS §12) and the publishable record (SLS §15) — S9."""

from __future__ import annotations

from scanner.domain.lifecycle.outcome import (
    HIT_RATE_STATES,
    Outcome,
    accounting,
)
from scanner.domain.lifecycle.payload import (
    PublicationDecision,
    SignalPayload,
    SuppressionReason,
    publication_checks,
)
from scanner.domain.lifecycle.state import (
    TERMINAL_STATES,
    Candle,
    Observation,
    SignalState,
    may_transition,
    observe,
)

__all__ = [
    "HIT_RATE_STATES",
    "TERMINAL_STATES",
    "Candle",
    "Observation",
    "Outcome",
    "PublicationDecision",
    "SignalPayload",
    "SignalState",
    "SuppressionReason",
    "accounting",
    "may_transition",
    "observe",
    "publication_checks",
]
