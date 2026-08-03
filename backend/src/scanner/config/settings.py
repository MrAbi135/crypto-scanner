"""Backward-compat shim.

`IngestSettings` and `load_ingest_settings` moved to `scanner.config.processes`
with the S0.2 config layer. This module re-exports them so existing imports of
`scanner.config.settings` keep working.
"""

from __future__ import annotations

from scanner.config.processes import IngestSettings, load_ingest_settings

__all__ = ["IngestSettings", "load_ingest_settings"]
