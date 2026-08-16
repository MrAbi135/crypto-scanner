"""Redis live ICT-zone working set (Sprint S6)."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import redis.asyncio as aioredis

from scanner.application.ports.ict_zones import (
    IctZoneRecord,
)
from scanner.shared import Timeframe

_PREFIX = "scanner:ict-zones:"


@dataclass(frozen=True, slots=True)
class IctZoneSnapshot:
    symbol: str
    timeframe: str
    zones: tuple[dict[str, Any], ...]


class RedisIctZoneStateStore:
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
        zones: tuple[IctZoneRecord, ...],
    ) -> None:
        ordered = sorted(
            zones,
            key=lambda zone: (
                -zone.created_index,
                zone.zone_type,
                zone.zone_id,
            ),
        )

        payload = {
            "symbol": symbol,
            "timeframe": timeframe.value,
            "zones": [
                {
                    "zone_id": zone.zone_id,
                    "zone_type": zone.zone_type,
                    "polarity": zone.polarity,
                    "state": zone.state,
                    "grade": zone.grade,
                    "band_low": str(zone.band_low),
                    "band_high": str(zone.band_high),
                    "refined_low": (None if zone.refined_low is None else str(zone.refined_low)),
                    "refined_high": (None if zone.refined_high is None else str(zone.refined_high)),
                    "created_index": (zone.created_index),
                    "confirmed_index": (zone.confirmed_index),
                    "created_at": (zone.created_at.isoformat()),
                    "updated_at": (zone.updated_at.isoformat()),
                    "parent_zone_id": (zone.parent_zone_id),
                    "dealing_range_id": (zone.dealing_range_id),
                    "stale_context": (zone.stale_context),
                    "gap_adjacent": (zone.gap_adjacent),
                    "origin_swept": (zone.origin_swept),
                }
                for zone in ordered
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
    ) -> IctZoneSnapshot | None:
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

        return IctZoneSnapshot(
            symbol=str(data["symbol"]),
            timeframe=str(data["timeframe"]),
            zones=tuple(
                dict(item)
                for item in data.get(
                    "zones",
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
