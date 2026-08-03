"""Structured logging foundation (TAD §17; S0.2 §12).

structlog renders JSON to stdout; the stdlib `logging` root is bridged through
the same renderer so uvicorn/alembic lines come out structured too. A
correlation-id contextvar and a secrets-redaction processor are active from the
first line. Nothing under `scanner.domain` may import this module (enforced by
the domain-purity import-linter contract — structlog is on the forbidden list).
"""

from __future__ import annotations

import logging
import sys
from contextvars import ContextVar

import structlog
from sentry_sdk.types import Event, Hint
from structlog.types import EventDict, Processor, WrappedLogger

_correlation_id: ContextVar[str | None] = ContextVar("scanner_correlation_id", default=None)

# Substrings that mark a value as secret; matched case-insensitively against keys.
_REDACT_HINTS = ("password", "token", "secret", "dsn", "api_key", "authorization")


def bind_correlation_id(value: str) -> None:
    """Bind a correlation id for the current context (edges set this)."""
    _correlation_id.set(value)


def _add_correlation_id(logger: WrappedLogger, name: str, event_dict: EventDict) -> EventDict:
    cid = _correlation_id.get()
    if cid is not None:
        event_dict.setdefault("correlation_id", cid)
    return event_dict


def _redact_secrets(logger: WrappedLogger, name: str, event_dict: EventDict) -> EventDict:
    for key in list(event_dict):
        if any(hint in key.lower() for hint in _REDACT_HINTS):
            event_dict[key] = "***redacted***"
    return event_dict


def _binder(key: str, value: str) -> Processor:
    def _add(logger: WrappedLogger, name: str, event_dict: EventDict) -> EventDict:
        if value:
            event_dict.setdefault(key, value)
        return event_dict

    return _add


def scrub_event(event: Event, hint: Hint) -> Event:
    """Sentry before_send hook: redact secret-looking keys (one redaction truth)."""
    _scrub(event)
    return event


def _scrub(node: object) -> None:
    if isinstance(node, dict):
        for key, value in node.items():
            if isinstance(key, str) and any(hint in key.lower() for hint in _REDACT_HINTS):
                node[key] = "***redacted***"
            else:
                _scrub(value)
    elif isinstance(node, list):
        for item in node:
            _scrub(item)


def _level_to_int(level: str) -> int:
    return logging.getLevelNamesMapping().get(level.upper(), logging.INFO)


def configure_logging(level: str, service: str, release: str = "") -> None:
    """Configure structlog + the stdlib bridge for one process. Idempotent."""
    level_no = _level_to_int(level)
    shared: list[Processor] = [
        structlog.contextvars.merge_contextvars,
        _binder("service", service),
        _binder("release", release),
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        _add_correlation_id,
        _redact_secrets,
    ]
    structlog.configure(
        processors=[*shared, structlog.stdlib.ProcessorFormatter.wrap_for_formatter],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.make_filtering_bound_logger(level_no),
        cache_logger_on_first_use=True,
    )
    formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=shared,
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            structlog.processors.JSONRenderer(),
        ],
    )
    handler = logging.StreamHandler(stream=sys.stdout)
    handler.setFormatter(formatter)
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level_no)
