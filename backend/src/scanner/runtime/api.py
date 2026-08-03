"""api process composition root (S0.2 §9).

Serves the three /internal/* routes only. No /api/v1 router exists this sprint;
business endpoints arrive from Sprint S10.
"""

from __future__ import annotations

from fastapi import FastAPI

from scanner.config import get_settings
from scanner.config.processes import ApiSettings
from scanner.runtime.wiring.bootstrap import bootstrap
from scanner.runtime.wiring.health import mount_health, run_asgi


def build_api_app(settings: ApiSettings) -> FastAPI:
    app = FastAPI(title="scanner-internal", docs_url=None, redoc_url=None, openapi_url=None)
    mount_health(app, settings)
    return app


def main() -> None:
    settings = get_settings("api")
    bootstrap(settings, "api")
    run_asgi(build_api_app(settings), settings.api_port)


if __name__ == "__main__":
    main()
