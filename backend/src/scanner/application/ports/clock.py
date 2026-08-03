"""Clock port — processes inject real time; tests inject FakeClock.

Domain and application code never read the wall clock directly (TAD §2.3).
"""

from __future__ import annotations

from datetime import datetime
from typing import Protocol


class Clock(Protocol):
    def now(self) -> datetime:
        """Current UTC time, timezone-aware."""
        ...
