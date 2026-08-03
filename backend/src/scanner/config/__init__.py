"""Typed process configuration (TAD §14; S0.2 §8).

`get_settings(process)` is the single config entry point; each process
constructs only its own settings. Missing/invalid `SCANNER_*` variables raise
pydantic `ValidationError` at construction, which the composition root lets
abort the boot.
"""

from __future__ import annotations

from typing import Literal, overload

from scanner.config.base import BaseProcessSettings
from scanner.config.processes import (
    ApiSettings,
    EngineSettings,
    IngestSettings,
    WorkerSettings,
    load_ingest_settings,
)

_BY_PROCESS: dict[str, type[BaseProcessSettings]] = {
    "api": ApiSettings,
    "ingest": IngestSettings,
    "engine": EngineSettings,
    "worker": WorkerSettings,
}


@overload
def get_settings(process: Literal["api"]) -> ApiSettings: ...
@overload
def get_settings(process: Literal["ingest"]) -> IngestSettings: ...
@overload
def get_settings(process: Literal["engine"]) -> EngineSettings: ...
@overload
def get_settings(process: Literal["worker"]) -> WorkerSettings: ...


def get_settings(process: str) -> BaseProcessSettings:
    try:
        settings_cls = _BY_PROCESS[process]
    except KeyError:
        raise ValueError(
            f"unknown process {process!r}; expected one of {sorted(_BY_PROCESS)}"
        ) from None
    return settings_cls()


__all__ = [
    "ApiSettings",
    "BaseProcessSettings",
    "EngineSettings",
    "IngestSettings",
    "WorkerSettings",
    "get_settings",
    "load_ingest_settings",
]
