"""Application port for T10's parameter-set registry (DDD T10, TAD §14)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol


@dataclass(frozen=True, slots=True)
class ParamSetRecord:
    """One deployed (engine, algo_version, param_set_version) triple."""

    engine: str
    algo_version: str
    param_set_version: str
    param_payload: str
    checksum: str
    sls_reference: str | None
    deployed_at: datetime


class ParamSetRepository(Protocol):
    async def get(
        self,
        engine: str,
        algo_version: str,
        param_set_version: str,
    ) -> ParamSetRecord | None:
        """The recorded set for this triple, or None if never deployed.

        Returns None as well for a row that predates verification -- one whose
        checksum is null. Boot fills those in rather than comparing against
        them, because a null checksum is the absence of a record, not a record
        of absence.
        """
        ...

    async def register(self, record: ParamSetRecord) -> None:
        """Record a triple deployed for the first time."""
        ...
