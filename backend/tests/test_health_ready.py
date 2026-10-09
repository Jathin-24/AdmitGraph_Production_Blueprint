"""Readiness probes must fail closed (audit P0-5 / PLAN P0-5).

`/health/ready` returned HTTP 200 with {"status": "unavailable"} while the
database was down, so every load balancer kept routing traffic to a backend
that could not serve it. It must be 503 on ping failure and 200 on success;
`/health` stays a pure liveness probe at 200.
"""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

from app.api.v1 import health as health_module
from app.main import app


def test_health_liveness_stays_200_without_database() -> None:
    response = TestClient(app).get("/api/v1/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_ready_returns_200_when_ping_succeeds(monkeypatch: Any) -> None:
    async def fake_ping(_session: Any) -> None:
        return None

    monkeypatch.setattr(health_module.health_repo, "ping", fake_ping)

    response = TestClient(app).get("/api/v1/health/ready")

    assert response.status_code == 200
    assert response.json() == {"status": "ready"}
    assert response.headers.get("X-Request-ID")


def test_ready_returns_503_when_ping_fails(monkeypatch: Any) -> None:
    async def boom(_session: Any) -> None:
        raise RuntimeError("database is down")

    monkeypatch.setattr(health_module.health_repo, "ping", boom)

    response = TestClient(app).get("/api/v1/health/ready")

    assert response.status_code == 503, "an unavailable database must not be 'ready'"
    assert response.json() == {"status": "unavailable"}
    assert response.headers.get("X-Request-ID")
