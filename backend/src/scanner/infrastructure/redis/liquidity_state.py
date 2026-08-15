"""Redis resting-liquidity snapshot store (Sprint S5)."""

from __future__ import annotations

import json
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

import redis.asyncio as aioredis

from scanner.application.ports.liquidity_detection import (
    LiquidityPoolRecord,
)
from scanner.shared import Timeframe

_PREFIX = "scanner:liquidity-state:"


@dataclass(frozen=True, slots=True)
class RestingLiquiditySnapshot:
    symbol: str
    timeframe: str
    pools: tuple[dict[str, Any], ...]


class RedisLiquidityStateStore:
    def __init__(
        self,
        client: aioredis.Redis,
    ) -> None:
        self._client = client

    @staticmethod
    def _key(
        symbol: str,
        timeframe: Timeframe,
    ) -> str:
        if not symbol:
            raise ValueError("symbol must not be empty")

        return f"{_PREFIX}{symbol}:{timeframe.value}"

    async def save(
        self,
        symbol: str,
        timeframe: Timeframe,
        pools: tuple[LiquidityPoolRecord, ...],
    ) -> None:
        ranked = sorted(
            pools,
            key=lambda pool: (
                -pool.strength,
                pool.price,
                pool.pool_id,
            ),
        )

        payload = {
            "symbol": symbol,
            "timeframe": timeframe.value,
            "pools": [
                {
                    "pool_id": pool.pool_id,
                    "side": pool.side,
                    "liquidity_class": pool.liquidity_class,
                    "source": pool.source,
                    "price": _decimal_str(pool.price),
                    "band_low": _decimal_str(pool.band_low),
                    "band_high": _decimal_str(pool.band_high),
                    "strength": _decimal_str(pool.strength),
                    "state": pool.state,
                    "member_count": pool.member_count,
                    "created_index": pool.created_index,
                    "created_at": pool.created_at.isoformat(),
                    "updated_at": pool.updated_at.isoformat(),
                }
                for pool in ranked
            ],
        }

        await self._client.set(
            self._key(
                symbol,
                timeframe,
            ),
            json.dumps(
                payload,
                sort_keys=True,
                separators=(",", ":"),
            ),
        )

    async def load(
        self,
        symbol: str,
        timeframe: Timeframe,
    ) -> RestingLiquiditySnapshot | None:
        value = await self._client.get(
            self._key(
                symbol,
                timeframe,
            )
        )

        if value is None:
            return None

        raw = value.decode("utf-8") if isinstance(value, bytes) else str(value)

        data: dict[str, Any] = json.loads(raw)

        return RestingLiquiditySnapshot(
            symbol=str(data["symbol"]),
            timeframe=str(data["timeframe"]),
            pools=tuple(
                dict(item)
                for item in data.get(
                    "pools",
                    [],
                )
            ),
        )

    async def delete(
        self,
        symbol: str,
        timeframe: Timeframe,
    ) -> None:
        await self._client.delete(
            self._key(
                symbol,
                timeframe,
            )
        )


def _decimal_str(
    value: Decimal,
) -> str:
    return format(value, "f")
