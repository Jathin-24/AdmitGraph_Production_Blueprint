from fastapi.testclient import TestClient

from app.main import app


def test_malformed_uuid_rejected() -> None:
    client = TestClient(app)
    response = client.get("/api/v1/evidence/not-a-uuid")
    assert response.status_code == 422
    body = response.json()
    assert body["error"]["code"] == "VALIDATION_ERROR"


def test_cors_unknown_origin_blocked() -> None:
    client = TestClient(app)
    response = client.options(
        "/api/v1/health",
        headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "GET"},
    )
    assert "access-control-allow-origin" not in {k.lower() for k in response.headers}


def test_oversized_payload_rejected() -> None:
    client = TestClient(app)
    big = "x" * (1_100_000)
    response = client.post(
        "/api/v1/documents",
        content=big,
        headers={"Content-Type": "application/json", "Content-Length": str(len(big))},
    )
    assert response.status_code == 413


def test_openapi_available() -> None:
    client = TestClient(app)
    response = client.get("/api/v1/openapi.json")
    assert response.status_code == 200
    assert response.json()["info"]["title"] == "AdmitGraph API"


def test_rate_limit_expensive_endpoint() -> None:
    """POST /research/runs is rate limited; exceeding the budget returns 429."""
    from app import main as main_module

    client = TestClient(app)
    status = 0
    body: dict = {}
    try:
        # Invalid payloads so requests never touch the database; the limiter
        # runs in middleware before validation, so counting is identical.
        for _ in range(main_module.settings.rate_limit_per_minute + 2):
            response = client.post("/api/v1/research/runs", json={"intake_year": "not-a-number"})
            status, body = response.status_code, response.json()
            if status == 429:
                break
    finally:
        main_module._rate_counters.clear()  # never leak budget into other tests
    assert status == 429, "rate limiter never triggered"
    assert body["error"]["code"] == "RATE_LIMITED"


def test_no_ssrf_or_cross_user_url_params() -> None:
    """No endpoint accepts raw URLs or foreign user/profile ids (SSRF + authz surface)."""
    client = TestClient(app)
    spec = client.get("/api/v1/openapi.json").json()
    risky_names = {"url", "uri", "webhook", "fetch_url", "target_url", "callback", "user_id"}
    found = [
        p["name"]
        for path in spec["paths"].values()
        for operation in path.values()
        if isinstance(operation, dict)
        for p in operation.get("parameters", [])
        if str(p.get("name", "")).lower() in risky_names
    ]
    assert found == [], f"endpoints expose risky fetch/cross-user parameters: {found}"


def test_logs_contain_no_secrets(caplog: object) -> None:
    import logging

    from app.core.config import get_settings

    settings = get_settings()
    client = TestClient(app)
    with caplog.at_level(logging.DEBUG):  # type: ignore[attr-defined]
        client.get("/api/v1/health")
        client.get("/api/v1/evidence/not-a-uuid")  # exercises the error path
        client.get("/api/v1/openapi.json")
    text = caplog.text  # type: ignore[attr-defined]
    for marker in ("sk-or-v1-", "gsk_", "b6b6df"):
        assert marker not in text, f"log output leaked credential fragment: {marker}"
    if settings.serpapi_api_key:
        assert settings.serpapi_api_key not in text, "log output leaked the SerpApi key"
