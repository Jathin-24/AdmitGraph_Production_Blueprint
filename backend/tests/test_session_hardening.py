"""Integration workstream close-out: session + reset-endpoint hardening.

Two handoffs from W3, proven without PostgreSQL:

1. A JWT minted BEFORE the user's last password change must be rejected with
   401 TOKEN_STALE (app/core/security.token_is_stale + the auth middleware in
   app/main.py), while newer sessions keep working.
2. POST /auth/forgot and /auth/verify-request must be rate limited (email-bomb
   vector): a burst returns 429 RATE_LIMITED, budgeted per caller IP AND per
   target email.

The DB-backed variant of (1) — a real users.password_changed_at read by the
middleware — lives in tests/integration/test_session_hardening_db.py.
"""

from __future__ import annotations

import time
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt as pyjwt
import pytest
from fastapi import Request
from fastapi.testclient import TestClient

from app.core import security
from app.main import app

# ------------------------------------------------- token_is_stale (unit)


def test_no_password_change_never_invalidates_a_token() -> None:
    old_token = {"iat": int(time.time()) - 3600}
    assert security.token_is_stale(old_token, None) is False


def test_token_issued_before_the_password_change_is_stale() -> None:
    changed = datetime.now(UTC) - timedelta(minutes=5)
    changed_epoch = int(changed.timestamp())
    assert security.token_is_stale({"iat": changed_epoch - 1}, changed) is True
    assert security.token_is_stale({"iat": changed_epoch - 3600}, changed) is True


def test_token_minted_after_the_password_change_is_fresh() -> None:
    changed = datetime.now(UTC) - timedelta(minutes=5)
    changed_epoch = int(changed.timestamp())
    assert security.token_is_stale({"iat": changed_epoch}, changed) is False
    assert security.token_is_stale({"iat": changed_epoch + 60}, changed) is False


def test_same_second_as_the_change_is_not_rejected() -> None:
    """Second-granularity floor: an immediate re-login must not be locked out."""
    iat = int(time.time())
    changed = datetime.fromtimestamp(iat + 0.9, tz=UTC)
    assert security.token_is_stale({"iat": iat}, changed) is False


def test_pwd_claim_wins_over_iat_when_present() -> None:
    changed = datetime.now(UTC) - timedelta(hours=1)
    changed_epoch = int(changed.timestamp())
    # Fresh pwd claim but ancient iat: pwd branch decides (still fresh).
    assert security.token_is_stale({"pwd": changed_epoch, "iat": 0}, changed) is False
    # Changed again since minting: mismatch → stale regardless of iat.
    assert security.token_is_stale({"pwd": changed_epoch - 1, "iat": changed_epoch + 99}, changed) is True


def test_undatable_claims_fail_closed() -> None:
    """A token we cannot date (no iat/pwd, or garbage) is a token we reject."""
    changed = datetime.now(UTC)
    assert security.token_is_stale({}, changed) is True
    assert security.token_is_stale({"iat": "not-a-number"}, changed) is True
    assert security.token_is_stale({"pwd": "not-a-number"}, changed) is True


def test_naive_password_changed_at_is_read_as_utc() -> None:
    changed_utc = datetime.now(UTC) - timedelta(minutes=5)
    naive = changed_utc.replace(tzinfo=None)
    assert security.token_is_stale({"iat": int(time.time()) - 3600}, naive) is True
    assert security.token_is_stale({"iat": int(time.time())}, naive) is False


# ------------------------------------------ create_token / decode_token


def test_create_token_adds_pwd_claim_only_when_given() -> None:
    uid = uuid.uuid4()
    changed = datetime.now(UTC) - timedelta(hours=1)

    with_pwd = security.decode_token(
        security.create_token(uid, "STUDENT", password_changed_at=changed)
    )
    assert with_pwd is not None
    assert with_pwd["pwd"] == int(changed.timestamp())
    assert security.token_is_stale(with_pwd, changed) is False

    # Existing callers (auth.py) do not pass the kwarg: no claim, unchanged shape.
    plain = security.decode_token(security.create_token(uid, "STUDENT"))
    assert plain is not None
    assert "pwd" not in plain
    assert isinstance(plain["iat"], int)
    assert plain["sub"] == str(uid)


def test_decode_token_forwards_iat_for_the_staleness_check() -> None:
    claims = security.decode_token(security.create_token(uuid.uuid4(), "STUDENT"))
    assert claims is not None
    assert abs(int(time.time()) - int(claims["iat"])) <= 5


# ------------------------------------------- middleware 401 TOKEN_STALE


def _signed_token(iat: int) -> str:
    """A validly signed token issued at a chosen moment (simulates age)."""
    now = int(time.time())
    return pyjwt.encode(
        {"sub": str(uuid.uuid4()), "role": "STUDENT", "iat": iat, "exp": now + 3600},
        security.jwt_secret(),
        algorithm=security.JWT_ALGORITHM,
    )


def _stub_password_changed_at(
    monkeypatch: pytest.MonkeyPatch, value: datetime | None
) -> None:
    from app import main as main_module

    async def _fetch(_user_id: uuid.UUID) -> datetime | None:
        return value

    monkeypatch.setattr(main_module, "_password_changed_at", _fetch)


def _authed(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_middleware_rejects_a_token_older_than_the_password_change(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    token = _signed_token(int(time.time()) - 3600)
    _stub_password_changed_at(monkeypatch, datetime.now(UTC) - timedelta(minutes=5))

    response = TestClient(app).get("/api/v1/health", headers=_authed(token))
    assert response.status_code == 401
    error = response.json()["error"]
    assert error["code"] == "TOKEN_STALE"
    assert error["request_id"]


def test_middleware_accepts_a_token_newer_than_the_password_change(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    token = _signed_token(int(time.time()))
    _stub_password_changed_at(monkeypatch, datetime.now(UTC) - timedelta(minutes=5))

    response = TestClient(app).get("/api/v1/health", headers=_authed(token))
    assert response.status_code == 200, response.text


def test_middleware_keeps_sessions_when_the_password_never_changed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """password_changed_at NULL (never changed) → age alone never 401s."""
    token = _signed_token(int(time.time()) - 3600)
    _stub_password_changed_at(monkeypatch, None)

    response = TestClient(app).get("/api/v1/health", headers=_authed(token))
    assert response.status_code == 200, response.text


def test_middleware_still_rejects_garbage_tokens_before_any_lookup() -> None:
    response = TestClient(app).get(
        "/api/v1/health", headers={"Authorization": "Bearer not.a.token"}
    )
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "UNAUTHENTICATED"


# --------------------------------- forgot / verify-request rate limiting


def _burst(path: str) -> list[Any]:
    """Hammer one reset endpoint until it 429s; returns the responses.

    Invalid payload on purpose: the limiter counts in middleware BEFORE
    validation, so the burst never touches the database or the outbox — the
    counting is identical to a real email-bomb burst (W1's limiter tests use
    the same trick).
    """
    from app import main as main_module

    client = TestClient(app)
    responses: list[Any] = []
    main_module._rate_counters.clear()
    try:
        for _ in range(main_module.RESET_REQUEST_LIMIT_PER_MINUTE + 2):
            response = client.post(path, json={"email": "not-an-email"})
            responses.append(response)
            if response.status_code == 429:
                break
    finally:
        main_module._rate_counters.clear()  # never leak budget into other tests
    return responses


def test_forgot_password_burst_hits_429() -> None:
    from app import main as main_module

    responses = _burst("/api/v1/auth/forgot")
    codes = [r.status_code for r in responses]
    assert codes == [400] * main_module.RESET_REQUEST_LIMIT_PER_MINUTE + [429]
    assert responses[-1].json()["error"]["code"] == "RATE_LIMITED"


def test_verify_request_burst_hits_429() -> None:
    from app import main as main_module

    responses = _burst("/api/v1/auth/verify-request")
    codes = [r.status_code for r in responses]
    assert codes == [400] * main_module.RESET_REQUEST_LIMIT_PER_MINUTE + [429]
    assert responses[-1].json()["error"]["code"] == "RATE_LIMITED"


def test_reset_budget_is_per_ip_and_per_target_email() -> None:
    """One host cannot spray many mailboxes; a botnet cannot hammer one."""
    from app import main as main_module

    def _request(body: str, ip: str) -> Request:
        scope: dict[str, Any] = {
            "type": "http",
            "method": "POST",
            "path": "/api/v1/auth/forgot",
            "headers": [],
            "client": (ip, 12345),
        }
        request = Request(scope)
        request._body = body.encode("utf-8")  # cached by the body-cap step
        return request

    limit = main_module.RESET_REQUEST_LIMIT_PER_MINUTE
    main_module._rate_counters.clear()
    try:
        # Same IP, rotating victims: the per-IP bucket stops the spray.
        for i in range(limit):
            body = f'{{"email": "victim-{i}@example.test"}}'
            assert main_module._over_reset_request_limit(_request(body, "10.0.0.1")) is False
        spray = main_module._over_reset_request_limit(
            _request('{"email": "one-more@example.test"}', "10.0.0.1")
        )
        assert spray is True

        # Rotating IPs, one victim: the per-email bucket stops the botnet.
        main_module._rate_counters.clear()
        for i in range(limit):
            assert (
                main_module._over_reset_request_limit(
                    _request('{"email": "victim@example.test"}', f"10.0.{i}.1")
                )
                is False
            )
        botnet = main_module._over_reset_request_limit(
            _request('{"email": "victim@example.test"}', "10.99.99.99")
        )
        assert botnet is True
    finally:
        main_module._rate_counters.clear()
