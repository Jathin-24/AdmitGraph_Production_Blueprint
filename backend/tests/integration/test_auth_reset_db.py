"""P2-14: password reset + email verification.

Integration tests against real PostgreSQL (fixture: tests/conftest.py).

Contract under test (PLAN.md cross-WS):

    POST /auth/forgot        {email}          -> 202 {status:"accepted"}
    POST /auth/reset         {token,password} -> 200 {status:"reset"}
    POST /auth/verify-request {email}         -> 202 {status:"accepted"}
    POST /auth/verify        {token}          -> 200 {status:"verified"}

plus the security properties: the endpoints never reveal whether an account
exists, only the SHA-256 hash of a token is stored, links are single-use, and
a token error never mentions the password (the frontend routes on that).
"""

from __future__ import annotations

import re
import uuid
from collections.abc import AsyncIterator
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.db.models import AuthToken, User, UserRole

_TOKEN_RE = re.compile(r"token=([A-Za-z0-9_\-]+)")


@pytest.fixture
async def api(db_session: Any) -> AsyncIterator[AsyncClient]:
    from app import main as main_module
    from app.main import app

    main_module._rate_counters.clear()
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        yield client


@pytest.fixture
def outbox(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, str]]:
    """Capture everything the auth router emails (never write files/SMTP)."""
    from app.api.v1 import auth as auth_module

    sent: list[dict[str, str]] = []

    async def _capture(to: str, subject: str, html: str, text: str) -> bool:
        sent.append({"to": to, "subject": subject, "html": html, "text": text})
        return True

    monkeypatch.setattr(auth_module, "send_email", _capture)
    return sent


async def _register(api: AsyncClient, prefix: str = "reset") -> dict[str, Any]:
    email = f"{prefix}-{uuid.uuid4().hex[:8]}@example.com"
    response = await api.post(
        "/api/v1/auth/register", json={"email": email, "password": "correct-horse-1"}
    )
    assert response.status_code == 201, response.text
    return {"email": email, "token": response.json()["token"]}


def _last_link(sent: list[dict[str, str]], needle: str) -> str:
    assert sent, "expected an email to be sent"
    for message in reversed(sent):
        blob = f"{message['text']}\n{message['html']}"
        match = _TOKEN_RE.search(blob)
        if match is not None and needle in blob:
            return match.group(1)
    raise AssertionError(f"no link containing {needle!r} in {len(sent)} message(s)")


def _error(response: Any) -> dict[str, Any]:
    body = response.json()
    assert "error" in body, body
    return body["error"]


async def _user(db_session: Any, email: str) -> User:
    return (await db_session.execute(select(User).where(User.email == email))).scalar_one()


# ------------------------------------------------------------------ forgot


async def test_forgot_always_answers_accepted(
    api: AsyncClient, outbox: list[dict[str, str]]
) -> None:
    known = await _register(api)
    responses = []
    for email in (known["email"], f"nobody-{uuid.uuid4().hex[:8]}@example.com"):
        response = await api.post("/api/v1/auth/forgot", json={"email": email})
        responses.append((response.status_code, response.json()))
    # Identical answers: existence never leaks through status or body.
    assert responses[0] == (202, {"status": "accepted"})
    assert responses[1] == (202, {"status": "accepted"})
    # Only the real account produced a link.
    assert len(outbox) == 1
    assert outbox[0]["to"] == known["email"]


async def test_forgot_for_a_passwordless_account_stays_silent(
    api: AsyncClient, outbox: list[dict[str, str]], db_session: Any
) -> None:
    """Seeded demo has no password hash: same 202, no email, no token row."""
    from app.services.profile import DEMO_EMAIL

    demo = (
        await db_session.execute(select(User).where(User.email == DEMO_EMAIL))
    ).scalar_one_or_none()
    if demo is None:
        db_session.add(User(email=DEMO_EMAIL, full_name="Demo Student", role=UserRole.STUDENT))
    else:
        demo.password_hash = None  # passwordless by construction
    await db_session.commit()

    response = await api.post("/api/v1/auth/forgot", json={"email": DEMO_EMAIL})
    assert response.status_code == 202, response.text
    assert response.json() == {"status": "accepted"}
    assert outbox == []


async def test_forgot_rejects_a_malformed_email(api: AsyncClient) -> None:
    response = await api.post("/api/v1/auth/forgot", json={"email": "not-an-email"})
    assert response.status_code == 400, response.text
    assert _error(response)["code"] == "VALIDATION_ERROR"


# ------------------------------------------------------------------- reset


async def test_reset_roundtrip_changes_the_password(
    api: AsyncClient, outbox: list[dict[str, str]], db_session: Any
) -> None:
    account = await _register(api)
    forgot = await api.post("/api/v1/auth/forgot", json={"email": account["email"]})
    assert forgot.status_code == 202, forgot.text
    raw = _last_link(outbox, "/reset-password")

    reset = await api.post(
        "/api/v1/auth/reset", json={"token": raw, "password": "a-brand-new-pass-1"}
    )
    assert reset.status_code == 200, reset.text
    assert reset.json() == {"status": "reset"}

    # New password in, old password out.
    login = await api.post(
        "/api/v1/auth/login",
        json={"email": account["email"], "password": "a-brand-new-pass-1"},
    )
    assert login.status_code == 200, login.text
    stale = await api.post(
        "/api/v1/auth/login",
        json={"email": account["email"], "password": "correct-horse-1"},
    )
    assert stale.status_code == 401, stale.text

    user = await _user(db_session, account["email"])
    assert user.password_changed_at is not None


async def test_reset_token_is_single_use(
    api: AsyncClient, outbox: list[dict[str, str]]
) -> None:
    account = await _register(api)
    await api.post("/api/v1/auth/forgot", json={"email": account["email"]})
    raw = _last_link(outbox, "/reset-password")

    first = await api.post(
        "/api/v1/auth/reset", json={"token": raw, "password": "a-brand-new-pass-1"}
    )
    assert first.status_code == 200, first.text

    second = await api.post(
        "/api/v1/auth/reset", json={"token": raw, "password": "another-pass-22"}
    )
    assert second.status_code == 400, second.text
    error = _error(second)
    assert error["code"] == "INVALID_TOKEN"
    assert "password" not in error["message"].lower()


async def test_a_new_link_invalidates_the_previous_one(
    api: AsyncClient, outbox: list[dict[str, str]]
) -> None:
    account = await _register(api)
    await api.post("/api/v1/auth/forgot", json={"email": account["email"]})
    first_token = _last_link(outbox, "/reset-password")

    await api.post("/api/v1/auth/forgot", json={"email": account["email"]})
    second_token = _last_link(outbox, "/reset-password")
    assert first_token != second_token

    stale = await api.post(
        "/api/v1/auth/reset", json={"token": first_token, "password": "a-brand-new-pass-1"}
    )
    assert stale.status_code == 400, stale.text
    assert _error(stale)["code"] == "INVALID_TOKEN"

    fresh = await api.post(
        "/api/v1/auth/reset", json={"token": second_token, "password": "a-brand-new-pass-1"}
    )
    assert fresh.status_code == 200, fresh.text


async def test_token_is_validated_before_the_password(api: AsyncClient) -> None:
    """Bad token + bad password: the answer must still be INVALID_TOKEN, and
    its message must not mention the password (the UI routes on that)."""
    for password in ("short", None):
        body: dict[str, Any] = {"token": "definitely-not-a-real-token"}
        if password is not None:
            body["password"] = password
        response = await api.post("/api/v1/auth/reset", json=body)
        assert response.status_code == 400, response.text
        error = _error(response)
        assert error["code"] == "INVALID_TOKEN", error
        assert "password" not in error["message"].lower()


async def test_password_problems_are_reported_with_the_password_word(
    api: AsyncClient, outbox: list[dict[str, str]]
) -> None:
    account = await _register(api)
    await api.post("/api/v1/auth/forgot", json={"email": account["email"]})
    raw = _last_link(outbox, "/reset-password")

    response = await api.post(
        "/api/v1/auth/reset", json={"token": raw, "password": "short"}
    )
    assert response.status_code == 400, response.text
    error = _error(response)
    assert error["code"] == "VALIDATION_ERROR"
    assert "Password" in error["message"]


async def test_the_raw_token_is_never_stored(
    api: AsyncClient, outbox: list[dict[str, str]], db_session: Any
) -> None:
    """A database dump must not yield a usable link: only the hash persists."""
    account = await _register(api)
    await api.post("/api/v1/auth/forgot", json={"email": account["email"]})
    raw = _last_link(outbox, "/reset-password")

    rows = (await db_session.execute(select(AuthToken))).scalars().all()
    assert rows, "a token row must exist"
    for row in rows:
        assert raw not in row.token_hash
        assert len(row.token_hash) == 64  # sha256 hex digest
    stored = [r for r in rows if r.token_hash == _sha256(raw)]
    assert len(stored) == 1


def _sha256(raw: str) -> str:
    import hashlib

    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


# ------------------------------------------------------------ verification


async def test_verify_roundtrip_stamps_email_verified_at(
    api: AsyncClient, outbox: list[dict[str, str]], db_session: Any
) -> None:
    account = await _register(api)
    request = await api.post("/api/v1/auth/verify-request", json={"email": account["email"]})
    assert request.status_code == 202, request.text
    assert request.json() == {"status": "accepted"}
    raw = _last_link(outbox, "/verify-email")

    verify = await api.post("/api/v1/auth/verify", json={"token": raw})
    assert verify.status_code == 200, verify.text
    assert verify.json() == {"status": "verified"}

    user = await _user(db_session, account["email"])
    assert user.email_verified_at is not None

    reused = await api.post("/api/v1/auth/verify", json={"token": raw})
    assert reused.status_code == 400, reused.text
    assert _error(reused)["code"] == "INVALID_TOKEN"


async def test_verify_request_never_reveals_existence(
    api: AsyncClient, outbox: list[dict[str, str]]
) -> None:
    known = await _register(api, "verify")
    unknown = f"nobody-{uuid.uuid4().hex[:8]}@example.com"
    first = await api.post("/api/v1/auth/verify-request", json={"email": known["email"]})
    second = await api.post("/api/v1/auth/verify-request", json={"email": unknown})
    assert (first.status_code, first.json()) == (second.status_code, second.json())
    assert first.status_code == 202
    assert len(outbox) == 1


async def test_tokens_are_bound_to_one_purpose(
    api: AsyncClient, outbox: list[dict[str, str]]
) -> None:
    """A verification link must not be spendable as a password reset."""
    account = await _register(api)
    await api.post("/api/v1/auth/verify-request", json={"email": account["email"]})
    verify_token = _last_link(outbox, "/verify-email")

    response = await api.post(
        "/api/v1/auth/reset", json={"token": verify_token, "password": "a-brand-new-pass-1"}
    )
    assert response.status_code == 400, response.text
    assert _error(response)["code"] == "INVALID_TOKEN"

    # The same link still works for its own purpose.
    verify = await api.post("/api/v1/auth/verify", json={"token": verify_token})
    assert verify.status_code == 200, verify.text
