"""Shared process bootstrap (S0.2 §9, S0.3 §8.1): settings -> logging -> metrics -> Sentry.

The single assembly point every entrypoint runs before serving. Sentry is
initialized here ONLY (composition-root law) and stays disabled unless
`SCANNER_SENTRY_DSN` is set (dev default: off). This is the future DI home.

`set_process_info` was minted with the metrics foundation and never called, so
`scanner_process_info` has never appeared on any of the four scrape targets.
It is a one-line gauge and the only thing that tells a dashboard which release
produced a series -- without it, "the numbers changed after the deploy" has no
deploy to point at.
"""

from __future__ import annotations

import sentry_sdk

from scanner.config.base import BaseProcessSettings
from scanner.infrastructure.observability.logging import configure_logging, scrub_event
from scanner.infrastructure.observability.metrics import set_process_info


def bootstrap(settings: BaseProcessSettings, service: str) -> None:
    configure_logging(settings.log_level, service, settings.release)
    set_process_info(service, settings.release)
    init_sentry(settings)


def init_sentry(settings: BaseProcessSettings) -> None:
    """Enable Sentry only when a DSN is configured; errors only, no tracing."""
    if not settings.sentry_dsn:
        return
    sentry_sdk.init(
        dsn=settings.sentry_dsn,
        environment=settings.env,
        release=settings.release,
        traces_sample_rate=0.0,
        before_send=scrub_event,
    )
