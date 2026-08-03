"""Health/metrics server behaviour (S0.2 §9).

Readiness is exercised against unreachable dependencies (ports that refuse
connections) — proving the 503 + per-dependency-detail path without a live
stack.
"""

from __future__ import annotations

from starlette.testclient import TestClient

from scanner.config.processes import ApiSettings
from scanner.runtime.api import build_api_app
from scanner.runtime.wiring.health import build_health_app


def _settings() -> ApiSettings:
    return ApiSettings(
        env="dev",
        db_dsn="postgresql+asyncpg://u:p@127.0.0.1:1/db",
        redis_url="redis://127.0.0.1:1/0",
    )


def test_live_is_200() -> None:
    client = TestClient(build_health_app(_settings()))
    response = client.get("/internal/health/live")
    assert response.status_code == 200
    assert response.json() == {"status": "live"}


def test_ready_is_503_with_dependency_detail_when_deps_down() -> None:
    client = TestClient(build_health_app(_settings()))
    response = client.get("/internal/health/ready")
    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "not_ready"
    assert set(body["dependencies"]) == {"db", "redis"}
    assert body["dependencies"]["db"].startswith("unreachable")


def test_metrics_renders_prometheus_exposition() -> None:
    client = TestClient(build_health_app(_settings()))
    response = client.get("/internal/metrics")
    assert response.status_code == 200
    assert "python_info" in response.text


def test_api_app_serves_the_same_internal_routes() -> None:
    client = TestClient(build_api_app(_settings()))
    assert client.get("/internal/health/live").status_code == 200
    # No business API surface exists this sprint.
    assert client.get("/api/v1/anything").status_code == 404
