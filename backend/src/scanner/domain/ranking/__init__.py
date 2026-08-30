"""Ranking Engine (SLS §9).

Turns per-setup confidence into a market-wide ordering. §9.1's weights live
with the confluence factors that use them; what is here is §9.2's cross-symbol
order and §9.3's display decay.
"""

from __future__ import annotations

from scanner.domain.ranking.decay import TTL_CANDLES, display_rank, ttl_candles
from scanner.domain.ranking.model import RankableSetup, tier_priority
from scanner.domain.ranking.order import rank

__all__ = [
    "TTL_CANDLES",
    "RankableSetup",
    "display_rank",
    "rank",
    "tier_priority",
    "ttl_candles",
]
