"""Request-weight budget authority (TDR §29): token bucket over Binance's
per-minute weight limit. One instance per venue — every REST call acquires
weight here first, so the platform can never stampede the API.
"""

from __future__ import annotations

import asyncio
import time

from scanner.shared import ValidationError


class RateBudget:
    def __init__(self, capacity: int = 1100, refill_per_second: float | None = None) -> None:
        # Default: Binance spot allows 1200 weight/min; we run at 1100 to
        # leave headroom for the S2 stream-resume bursts (budget authority
        # is shared, so batch work must never consume the whole ceiling).
        if capacity <= 0:
            raise ValidationError(f"rate budget capacity must be positive, got {capacity}")
        self._capacity = capacity
        self._refill = refill_per_second if refill_per_second is not None else capacity / 60.0
        self._tokens = float(capacity)
        self._updated = time.monotonic()
        self._lock = asyncio.Lock()

    async def acquire(self, weight: int) -> None:
        """Block until `weight` tokens are available, then consume them."""
        if weight <= 0 or weight > self._capacity:
            raise ValidationError(f"weight {weight} outside (0, {self._capacity}]")
        async with self._lock:
            while True:
                self._refill_now()
                if self._tokens >= weight:
                    self._tokens -= weight
                    return
                deficit = weight - self._tokens
                await asyncio.sleep(deficit / self._refill)

    def penalize(self, seconds: float) -> None:
        """Empty the bucket for a cool-down (429/418 Retry-After handling)."""
        self._tokens = 0.0
        self._updated = time.monotonic() + max(0.0, seconds)

    def _refill_now(self) -> None:
        now = time.monotonic()
        if now <= self._updated:
            return
        self._tokens = min(self._capacity, self._tokens + (now - self._updated) * self._refill)
        self._updated = now
