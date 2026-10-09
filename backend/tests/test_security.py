import hmac
import logging
import os
import time
import uuid
from pathlib import Path

import jwt
import pytest
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
    from app import main as main_module

    client = TestClient(app)
    big = "x" * (main_module.MAX_BODY_BYTES + 1_000_000)
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


def test_middleware_runtime_order_matches_documented_spec() -> None:
    """CORS -> request_id -> auth -> body_rate -> router (BACKEND_SPEC).

    Starlette prepends every add_middleware, so user_middleware[0] runs
    OUTERMOST; request_id must wrap auth and body_rate so their 401/413/429
    responses still get X-Request-ID.
    """
    from fastapi.middleware.cors import CORSMiddleware

    stack = list(app.user_middleware)
    assert stack[0].cls is CORSMiddleware, "CORS must be the outermost middleware"
    dispatch_names = [entry.kwargs["dispatch"].__name__ for entry in stack[1:]]
    assert dispatch_names == [
        "request_id_middleware",
        "auth_context_middleware",
        "body_size_and_rate_limit",
    ]


def test_error_responses_carry_request_id_header_and_body() -> None:
    """401 (auth), 413 (body) and 429 (rate limit) all carry the request id."""
    from app import main as main_module

    client = TestClient(app)
    try:
        # 401: invalid bearer token, rejected by the auth middleware.
        unauthorized = client.get(
            "/api/v1/auth/me",
            headers={"Authorization": "Bearer not.a.token", "X-Request-ID": "req-401-test"},
        )
        assert unauthorized.status_code == 401
        assert unauthorized.headers.get("X-Request-ID") == "req-401-test"
        assert unauthorized.json()["error"]["request_id"] == "req-401-test"
        assert unauthorized.json()["error"]["code"] == "UNAUTHENTICATED"

        # 413: oversized payload, rejected by the body middleware.
        big = "x" * (main_module.MAX_BODY_BYTES + 1_000_000)
        too_large = client.post(
            "/api/v1/documents",
            content=big,
            headers={
                "Content-Type": "application/json",
                "Content-Length": str(len(big)),
                "X-Request-ID": "req-413-test",
            },
        )
        assert too_large.status_code == 413
        assert too_large.headers.get("X-Request-ID") == "req-413-test"
        assert too_large.json()["error"]["request_id"] == "req-413-test"

        # 429: rate limit, rejected by the rate-limit middleware.
        main_module._rate_counters.clear()
        limited = None
        for _ in range(main_module.settings.rate_limit_per_minute + 2):
            limited = client.post(
                "/api/v1/research/runs",
                json={"intake_year": "not-a-number"},
                headers={"X-Request-ID": "req-429-test"},
            )
            if limited.status_code == 429:
                break
        assert limited is not None and limited.status_code == 429, "rate limiter never triggered"
        assert limited.headers.get("X-Request-ID") == "req-429-test"
        body = limited.json()
        assert body["error"]["code"] == "RATE_LIMITED"
        assert body["error"]["request_id"] == "req-429-test"
    finally:
        main_module._rate_counters.clear()  # never leak budget into other tests


def test_rate_limit_covers_plan_and_evidence_recheck() -> None:
    """POST /research/plan and POST /evidence/{id}/recheck are budget-limited."""
    from app import main as main_module

    client = TestClient(app)
    endpoints = (
        ("/api/v1/research/plan", {"json": {"intake_year": "not-a-number"}}),
        ("/api/v1/evidence/not-a-uuid/recheck", {}),
    )
    for path, kwargs in endpoints:
        status = 0
        try:
            # Invalid payloads: the limiter runs in middleware before routing,
            # so counting is identical without touching the database.
            for _ in range(main_module.settings.rate_limit_per_minute + 2):
                response = client.post(path, **kwargs)
                status = response.status_code
                if status == 429:
                    break
        finally:
            main_module._rate_counters.clear()
        assert status == 429, f"rate limiter never triggered for {path}"


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


# ---------------------------------------------------------------------------
# P0-1: JWT signing key must not be derivable from committed config,
# and production must fail closed.
# ---------------------------------------------------------------------------


def test_generated_dev_key_not_derivable_and_warns_loudly(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """The fallback key must be random — the old hmac("admitgraph-dev",
    DATABASE_URL) derivation was recomputable by anyone with the repo."""
    from app.core import security
    from app.core.config import get_settings

    settings = get_settings()
    secret_file = tmp_path / "jwt_secret"
    monkeypatch.setattr(settings, "jwt_secret", "")
    monkeypatch.setattr(settings, "app_env", "development")
    monkeypatch.setattr(security, "_generated_secret", None)
    monkeypatch.setattr(security, "_DEV_SECRET_FILE", secret_file)

    with caplog.at_level(logging.WARNING):
        secret = security.jwt_secret()

    old_derived = hmac.new(
        b"admitgraph-dev", settings.database_url.encode(), "sha256"
    ).hexdigest()
    assert secret != old_derived, "key must not reproduce the old hmac derivation"
    assert settings.database_url not in secret
    assert len(secret) >= 64, "generated key must be high-entropy"
    # Loud warning + restart-stable persistence in the gitignored file (0600).
    assert "JWT_SECRET" in caplog.text
    assert secret_file.exists()
    assert secret_file.read_text(encoding="utf-8") == secret
    if os.name == "posix":
        assert secret_file.stat().st_mode & 0o777 == 0o600

    # Same process reuses the same key (sessions keep working) ...
    assert security.jwt_secret() == secret
    # ... and a fresh process reads it back from the file.
    monkeypatch.setattr(security, "_generated_secret", None)
    assert security.jwt_secret() == secret


def test_tokens_signed_with_different_secrets_do_not_cross_decode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two jwt_secret settings must produce tokens the other key cannot read."""
    from app.core import security
    from app.core.config import get_settings

    settings = get_settings()
    user_id = uuid.uuid4()
    monkeypatch.setattr(settings, "jwt_secret", "secret-alpha")
    token_alpha = security.create_token(user_id, "STUDENT")

    monkeypatch.setattr(settings, "jwt_secret", "secret-beta")
    token_beta = security.create_token(user_id, "STUDENT")
    assert security.decode_token(token_alpha) is None

    monkeypatch.setattr(settings, "jwt_secret", "secret-alpha")
    assert security.decode_token(token_beta) is None
    claims = security.decode_token(token_alpha)
    assert claims is not None
    assert claims["sub"] == str(user_id)


def test_production_without_secret_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.core import security
    from app.core.config import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "jwt_secret", "")
    monkeypatch.setattr(settings, "app_env", "production")
    with pytest.raises(RuntimeError, match="JWT_SECRET"):
        security.jwt_secret()
    # The fail-closed check must win even if a dev key was already cached.
    monkeypatch.setattr(security, "_generated_secret", "cached-dev-key")
    with pytest.raises(RuntimeError, match="JWT_SECRET"):
        security.jwt_secret()


def test_app_refuses_to_start_in_production_without_secret(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Lifespan calls jwt_secret(): boot must abort, not run with an ephemeral key."""
    from app.core.config import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "jwt_secret", "")
    monkeypatch.setattr(settings, "app_env", "production")
    with pytest.raises(RuntimeError, match="JWT_SECRET"):
        with TestClient(app):
            pass  # pragma: no cover - lifespan must never complete


def test_token_forged_with_old_derived_key_is_rejected(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A token signed with the old publicly-derivable key must not decode."""
    from app.core import security
    from app.core.config import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "jwt_secret", "")
    monkeypatch.setattr(settings, "app_env", "development")
    monkeypatch.setattr(security, "_generated_secret", None)
    monkeypatch.setattr(security, "_DEV_SECRET_FILE", tmp_path / "jwt_secret")

    old_key = hmac.new(b"admitgraph-dev", settings.database_url.encode(), "sha256").hexdigest()
    now = int(time.time())
    forged = jwt.encode(
        {"sub": str(uuid.uuid4()), "role": "ADMIN", "iat": now, "exp": now + 3600},
        old_key,
        algorithm="HS256",
    )
    assert security.decode_token(forged) is None


# ---------------------------------------------------------------------------
# Security headers on API responses (P2-21).
# ---------------------------------------------------------------------------


def test_security_headers_present_on_api_responses() -> None:
    client = TestClient(app)
    ok = client.get("/api/v1/health")
    assert ok.status_code == 200
    assert ok.headers["X-Content-Type-Options"] == "nosniff"
    assert ok.headers["X-Frame-Options"] == "DENY"
    assert ok.headers["Referrer-Policy"] == "no-referrer"
    assert ok.headers["Content-Security-Policy"] == "default-src 'self'"

    # Error responses carry them too (middleware wraps router + error handlers).
    invalid = client.get("/api/v1/evidence/not-a-uuid")
    assert invalid.status_code == 422
    assert invalid.headers["X-Content-Type-Options"] == "nosniff"
    assert invalid.headers["Content-Security-Policy"] == "default-src 'self'"

    unauthorized = client.get(
        "/api/v1/auth/me", headers={"Authorization": "Bearer not.a.token"}
    )
    assert unauthorized.status_code == 401
    assert unauthorized.headers["X-Frame-Options"] == "DENY"
    assert unauthorized.headers["Referrer-Policy"] == "no-referrer"


# ---------------------------------------------------------------------------
# Body cap enforced while streaming, not just from Content-Length.
# ---------------------------------------------------------------------------


def test_chunked_body_over_limit_rejected_without_content_length() -> None:
    """Chunked uploads carry no Content-Length — the cap must still apply."""
    from app import main as main_module

    client = TestClient(app)

    def chunks():
        for _ in range(main_module.MAX_BODY_BYTES // 1_000_000 + 2):
            yield b"x" * 1_000_000

    response = client.post(
        "/api/v1/documents",
        content=chunks(),
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == 413
    assert response.json()["error"]["code"] == "PAYLOAD_TOO_LARGE"
    # Proof the request really carried no Content-Length (chunked instead).
    assert response.request.headers.get("content-length") is None


def test_body_cap_enforced_when_content_length_understates() -> None:
    """A client can lie about Content-Length — bytes read are what count."""
    from app import main as main_module

    client = TestClient(app)
    body = b"x" * (main_module.MAX_BODY_BYTES + 1_000)
    response = client.post(
        "/api/v1/documents",
        content=body,
        headers={"Content-Type": "application/json", "Content-Length": "10"},
    )
    assert response.status_code == 413
    assert response.json()["error"]["code"] == "PAYLOAD_TOO_LARGE"


# ---------------------------------------------------------------------------
# Rate limiter: bounded memory, per-user buckets, GET coverage.
# ---------------------------------------------------------------------------


def test_rate_limit_counters_evict_expired_entries() -> None:
    """Stale buckets are reclaimed so the in-process map stays bounded."""
    from app import main as main_module

    client = TestClient(app)
    main_module._rate_counters.clear()
    monkey_cap = 5
    original_cap = main_module.RATE_LIMIT_MAX_KEYS
    stale_start = time.time() - main_module.RATE_LIMIT_WINDOW_SECONDS - 1
    try:
        main_module.RATE_LIMIT_MAX_KEYS = monkey_cap
        for i in range(50):
            main_module._rate_counters[f"ip:stale-{i}"] = (1, stale_start)
        response = client.post("/api/v1/research/runs", json={"intake_year": "nope"})
        assert response.status_code != 429  # fresh key, default budget
        stale_left = [k for k in main_module._rate_counters if k.startswith("ip:stale-")]
        assert stale_left == [], f"expired buckets were never evicted: {stale_left[:5]}"
        assert len(main_module._rate_counters) <= monkey_cap, "memory bound not respected"
    finally:
        main_module.RATE_LIMIT_MAX_KEYS = original_cap
        main_module._rate_counters.clear()


def test_rate_limit_buckets_are_per_authenticated_user() -> None:
    """A valid bearer token gets its own bucket; others keep their budget."""
    from app import main as main_module
    from app.core.security import create_token

    client = TestClient(app)
    main_module._rate_counters.clear()
    token_a = create_token(uuid.uuid4(), "STUDENT")
    token_b = create_token(uuid.uuid4(), "STUDENT")
    original_limit = main_module.settings.rate_limit_per_minute

    def attempt(token: str | None) -> int:
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        response = client.post(
            "/api/v1/research/runs", json={"intake_year": "nope"}, headers=headers
        )
        return response.status_code

    try:
        main_module.settings.rate_limit_per_minute = 2
        first, second, third = attempt(token_a), attempt(token_a), attempt(token_a)
        assert first != 429 and second != 429
        assert third == 429, "user A never hit its budget"
        # User B and the anonymous IP still have untouched buckets.
        assert attempt(token_b) != 429, "user B must not share user A's bucket"
        assert attempt(None) != 429, "anonymous callers must not share the user bucket"
        # ... while user A stays locked for the rest of the window.
        assert attempt(token_a) == 429
    finally:
        main_module.settings.rate_limit_per_minute = original_limit
        main_module._rate_counters.clear()


def test_rate_limit_applies_to_get_provider_paths(monkeypatch: pytest.MonkeyPatch) -> None:
    """GET endpoints that spend provider budget inherit the same budget."""
    from app import main as main_module

    client = TestClient(app)
    main_module._rate_counters.clear()
    original_limit = main_module.settings.rate_limit_per_minute
    try:
        monkeypatch.setattr(main_module, "_RATE_LIMITED_GET_PATHS", frozenset({"/api/v1/health"}))
        main_module.settings.rate_limit_per_minute = 2
        codes = [client.get("/api/v1/health").status_code for _ in range(3)]
        assert codes == [200, 200, 429]
    finally:
        main_module.settings.rate_limit_per_minute = original_limit
        main_module._rate_counters.clear()
