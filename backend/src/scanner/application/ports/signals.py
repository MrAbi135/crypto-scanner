"""Application port for T17 `detection.signals` (DDD T17, SLS §12, §15)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Protocol

from scanner.shared import Timeframe


@dataclass(frozen=True, slots=True)
class SignalRecord:
    """One published signal, sealed at creation.

    §12.1: "immutable core: evidence, zones, levels never mutate
    post-creation". `payload` is the §15.2 snapshot and `payload_hash` is the
    seal over exactly that string, so tamper evidence survives even if the
    columns beside it are read instead.
    """

    signal_id: str
    setup_id: str
    symbol: str
    timeframe: Timeframe
    direction: str
    archetype: str
    grade: str
    final_confidence: Decimal
    entry_proximal: Decimal
    entry_distal: Decimal
    invalidation_level: Decimal
    target_bands: str
    published_at: datetime
    ttl_candles: int
    algo_version: str
    param_set_version: str
    payload: str
    payload_hash: str
    dedup_key: str


class SignalRepository(Protocol):
    async def append(self, signal: SignalRecord) -> bool:
        """Publish one signal. False when the id already existed.

        Insert-once: T17's read/write pattern, and Constitution §45.5 makes
        the immutability constitutional. There is deliberately no update
        method on this port -- an interface that cannot express a mutation is
        a stronger guarantee than one that merely declines to use it.
        """
        ...

    async def latest_for_dedup_key(self, dedup_key: str) -> SignalRecord | None:
        """The most recently published signal on this key, if any.

        §15.3(4)'s "dedup key clear" needs the newest one and its
        `published_at`; whether it is still inside its TTL is §12.5's
        arithmetic and belongs to the caller, not to a query.
        """
        ...

    async def get(self, signal_id: str) -> SignalRecord | None: ...

    async def recent(
        self,
        *,
        limit: int,
        symbol: str | None = None,
        timeframe: Timeframe | None = None,
    ) -> tuple[SignalRecord, ...]:
        """The newest published signals first, for an operator reading a tail."""
        ...

    async def scan(self, *, batch: int = 500) -> list[SignalRecord]:
        """Every signal, oldest first, for a full audit pass.

        Separate from `recent` because the two want opposite orders and
        opposite bounds: a tail wants the newest few, an audit wants all of
        them and must not miss a row published while it runs. Oldest-first
        keyset paging gives that -- new rows land after the cursor.
        """
        ...
