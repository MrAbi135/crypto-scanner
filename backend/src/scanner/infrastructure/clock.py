"""System clock infrastructure adapter."""

from __future__ import annotations

from datetime import UTC, datetime


class SystemClock:
    """Production clock backed by the system UTC clock."""

    def now(self) -> datetime:
        return datetime.now(UTC)
