"""Symbol registry value objects (SLS §1, DDD T1).

S1 scope: registry facts only. Tiering, hysteresis and quarantine
mechanics are the S3 universe manager's concern.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum

from scanner.domain.common.exclusions import ExclusionReason
from scanner.shared import require


class SymbolStatus(str, Enum):
    """Lifecycle per SLS §1: quarantine → active → delisting → delisted.

    EXCLUDED sits outside that lifecycle: §1.3's hard exclusions are evaluated
    before any tier, so no liquidity reading moves a symbol in or out of it.
    """

    QUARANTINE = "QUARANTINE"
    ACTIVE = "ACTIVE"
    DELISTING = "DELISTING"
    DELISTED = "DELISTED"
    EXCLUDED = "EXCLUDED"


@dataclass(frozen=True, slots=True)
class Symbol:
    id: str  # ULID
    venue: str
    exchange_symbol: str  # e.g. "BTCUSDT"
    base_asset: str
    quote_asset: str
    status: SymbolStatus
    first_seen_at: datetime
    exclusion_reason: ExclusionReason | None = None

    def __post_init__(self) -> None:
        require(
            (self.status is SymbolStatus.EXCLUDED) == (self.exclusion_reason is not None),
            "SYMBOL_EXCLUSION_REASON",
            f"{self.exchange_symbol}: an EXCLUDED symbol needs a reason, and only it has one",
        )
        require(bool(self.exchange_symbol), "SYMBOL_EMPTY", "exchange_symbol must be non-empty")
        require(
            self.exchange_symbol == f"{self.base_asset}{self.quote_asset}",
            "SYMBOL_ASSET_MISMATCH",
            f"{self.exchange_symbol}: base+quote ({self.base_asset}+{self.quote_asset}) mismatch",
        )
