"""ingest process composition root (S0.2 §9).

Skeleton this sprint: settings -> logging -> health server. The data-collection
organs (Binance adapters, backfill/stream orchestration) arrive from Sprint S1+.
"""

from __future__ import annotations

from scanner.config import get_settings
from scanner.runtime.wiring.bootstrap import bootstrap
from scanner.runtime.wiring.health import build_health_app, run_asgi


def main() -> None:
    settings = get_settings("ingest")
    bootstrap(settings, "ingest")
    run_asgi(build_health_app(settings), settings.health_port)


if __name__ == "__main__":
    main()
