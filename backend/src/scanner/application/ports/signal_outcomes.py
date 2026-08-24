"""Application port for T19 `detection.signal_outcomes` (DDD T19, SLS §12.4)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Protocol


@dataclass(frozen=True, slots=True)
class SignalOutcomeRecord:
    """One resolved signal's terminal accounting.

    §12.4: "outcomes are immutable and feed §28-Constitution signal-quality
    metrics per algo version". There is no update method on the port below for
    the same reason there is none on T17's: an interface that cannot express a
    mutation is a stronger guarantee than one that declines to use it.
    """

    signal_id: str
    outcome: str
    resolved_at: datetime
    elapsed_candles: int
    mfe_r: Decimal
    mae_r: Decimal
    excluded_from_stats: bool
    resolution_evidence: str


class SignalOutcomeRepository(Protocol):
    async def append(self, outcome: SignalOutcomeRecord) -> bool:
        """Record one resolution. False when the signal already has one."""
        ...

    async def get(self, signal_id: str) -> SignalOutcomeRecord | None: ...
