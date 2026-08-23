"""Application port for T16 `detection.setups` (DDD T16, SLS §8.6)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Protocol

from scanner.shared import Timeframe


@dataclass(frozen=True, slots=True)
class SetupRecord:
    """One evaluated candidate that cleared §8.2's gates.

    Both halves of T16's purpose are here: `floor_passed` separates the
    published from the below-floor, and §8.6 keeps the second group on purpose
    -- *"floor rejects are recorded calibration data"*. A table holding only
    the published ones could never answer why a market produced none.
    """

    setup_id: str
    symbol: str
    timeframe: Timeframe
    direction: str
    archetype: str | None
    gate_results: str
    factor_scores: str
    adjustments: str
    base_confidence: Decimal
    final_confidence: Decimal
    floor_passed: bool
    algo_version: str
    evaluated_at: datetime
    evidence: str


class SetupRepository(Protocol):
    async def append(self, setup: SetupRecord) -> bool:
        """Store one candidate. False when the row already existed.

        Append-only: T16's read/write pattern says so, and a re-evaluated
        candle must not rewrite what an earlier pass concluded. The id is a
        hash of the candidate's identity, so a repeat is a repeat.
        """
        ...

    async def list_at(
        self,
        symbols: tuple[str, ...],
        timeframe: Timeframe,
        evaluated_at: datetime,
    ) -> tuple[SetupRecord, ...]:
        """Every stored candidate for those symbols at one close."""
        ...
