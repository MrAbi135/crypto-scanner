"""Market data context: ingestion orchestration (TAD §9, S1 batch slice)."""

from scanner.application.marketdata.backfill import BackfillReport, BackfillService
from scanner.application.marketdata.continuity import ContinuityReport, verify_continuity
from scanner.application.marketdata.symbol_sync import SymbolSyncReport, SymbolSyncService
from scanner.application.marketdata.validation import (
    BatchValidationResult,
    ValidationFinding,
    validate_batch,
    verify_aggregation,
)

__all__ = [
    "BackfillReport",
    "BackfillService",
    "BatchValidationResult",
    "ContinuityReport",
    "SymbolSyncReport",
    "SymbolSyncService",
    "ValidationFinding",
    "validate_batch",
    "verify_aggregation",
    "verify_continuity",
]
