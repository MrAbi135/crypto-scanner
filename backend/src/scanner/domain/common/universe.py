"""Universe liquidity tier domain primitives (SLS §1.4)."""

from __future__ import annotations

from enum import Enum


class UniverseTier(str, Enum):
    """Liquidity eligibility tier."""

    T1 = "T1"
    T2 = "T2"
    T3 = "T3"
    INELIGIBLE = "INELIGIBLE"
    