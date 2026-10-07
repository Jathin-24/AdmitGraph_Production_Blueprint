"""Contract tests: OpenAPI matches API_CONTRACT, error envelope, no secrets."""

from __future__ import annotations

import re

from fastapi.testclient import TestClient

from app.main import app

REQUIRED_PATHS = [
    "/api/v1/health",
    "/api/v1/me/profile",
    "/api/v1/me/profile/completion",
    "/api/v1/onboarding/schema",
    "/api/v1/onboarding/answers",
    "/api/v1/research/runs",
    "/api/v1/research/runs/{run_id}",
    "/api/v1/research/runs/{run_id}/events",
    "/api/v1/research/runs/{run_id}/cancel",
    "/api/v1/research/plan",
    "/api/v1/programs",
    "/api/v1/programs/{program_id}",
    "/api/v1/programs/{program_id}/requirements",
    "/api/v1/programs/{program_id}/evidence",
    "/api/v1/programs/{program_id}/save",
    "/api/v1/strategies",
    "/api/v1/strategies/{strategy_id}",
    "/api/v1/strategies/{strategy_id}/portfolio",
    "/api/v1/strategies/{strategy_id}/risks",
    "/api/v1/strategies/{strategy_id}/roadmap",
    "/api/v1/strategies/{strategy_id}/evidence-health",
    "/api/v1/strategies/{strategy_id}/simulate",
    "/api/v1/strategies/{strategy_id}/export/pdf",
    "/api/v1/monitor/subscriptions",
    "/api/v1/monitor/subscriptions/{subscription_id}/check",
    "/api/v1/documents",
    "/api/v1/documents/{document_id}",
    "/api/v1/evidence/{evidence_id}",
    "/api/v1/evidence/{evidence_id}/conflicts",
    "/api/v1/admin/search-usage",
    "/api/v1/admin/research-runs",
]


def _spec() -> dict:
    with TestClient(app) as client:
        response = client.get("/api/v1/openapi.json")
    assert response.status_code == 200
    return response.json()


def test_contract_paths_exist() -> None:
    paths = set(_spec()["paths"])
    missing = [p for p in REQUIRED_PATHS if p not in paths]
    assert missing == [], f"Missing contract endpoints: {missing}"


def test_no_provider_secrets_in_openapi() -> None:
    raw = str(_spec())
    for secret_marker in ("sk-or-v1-", "gsk_", "b6b6df", "serpapi.com", "api_key"):
        assert secret_marker not in raw, f"OpenAPI leaks marker: {secret_marker}"


def test_validation_error_envelope_shape() -> None:
    with TestClient(app) as client:
        response = client.get("/api/v1/evidence/not-a-uuid")
    assert response.status_code == 422
    body = response.json()
    assert set(body["error"].keys()) >= {"code", "message", "details", "request_id"}
    assert body["error"]["code"] == "VALIDATION_ERROR"
    assert body["error"]["request_id"]


def test_every_response_carries_request_id() -> None:
    with TestClient(app) as client:
        ok = client.get("/api/v1/health")
        bad = client.get("/api/v1/evidence/not-a-uuid")
    assert ok.headers.get("X-Request-ID")
    assert bad.headers.get("X-Request-ID")


def test_page_size_cap_enforced() -> None:
    with TestClient(app) as client:
        response = client.get("/api/v1/programs?page_size=1000")
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


def test_unknown_scenario_uses_error_envelope() -> None:
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/strategies/00000000-0000-0000-0000-000000000000/simulate",
            json={"scenario": "MADE_UP"},
        )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


def test_frontend_api_paths_resolve_against_openapi() -> None:
    """Frontend api.ts literals must reference paths that exist in the spec."""
    from pathlib import Path

    api_ts = Path(__file__).resolve().parents[2] / "frontend" / "app" / "lib" / "api.ts"
    if not api_ts.exists():
        import pytest

        pytest.skip("frontend not present in this checkout")
    source = api_ts.read_text(encoding="utf-8")
    literals = re.findall(r'apiFetch(?:<[^>]*>)?\(\s*[`"\']([^`"\']+)', source)

    def canonical(path: str) -> str:
        # Collapses both "${runId}" (frontend) and "{run_id}" (OpenAPI) to "{}".
        return re.sub(r"\$?\{[^}]+\}", "{}", path)

    paths = {canonical(p) for p in _spec()["paths"]}
    unresolved = []
    for lit in literals:
        path = lit.split("?")[0]
        full = canonical(f"/api/v1{path}")
        if full not in paths:
            unresolved.append(full)
    assert literals, "expected api.ts to call apiFetch at least once"
    assert unresolved == [], f"frontend calls unknown endpoints: {unresolved}"
