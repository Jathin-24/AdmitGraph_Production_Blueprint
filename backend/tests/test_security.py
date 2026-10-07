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
