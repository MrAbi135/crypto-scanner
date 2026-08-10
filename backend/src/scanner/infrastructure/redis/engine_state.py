"""Redis-backed engine state snapshots (Sprint S4)."""

from __future__ import annotations

import redis.asyncio as aioredis

_PREFIX = "scanner:engine-state:"


class RedisEngineStateStore:
    def __init__(
        self,
        client: aioredis.Redis,
    ) -> None:
        self._client = client

    @staticmethod
    def _key(
        context_key: str,
    ) -> str:
        if not context_key:
            raise ValueError(
                "context_key must not be empty"
            )

        return f"{_PREFIX}{context_key}"

    async def load(
        self,
        context_key: str,
    ) -> str | None:
        value = await self._client.get(
            self._key(context_key)
        )

        if value is None:
            return None

        if isinstance(value, bytes):
            return value.decode("utf-8")

        return str(value)

    async def save(
        self,
        context_key: str,
        payload: str,
    ) -> None:
        await self._client.set(
            self._key(context_key),
            payload,
        )

    async def delete(
        self,
        context_key: str,
    ) -> None:
        await self._client.delete(
            self._key(context_key)
        )
