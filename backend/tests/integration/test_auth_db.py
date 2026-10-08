"""Auth + authorization flows against real PostgreSQL (tests/conftest.py).

Covers the security requirements in tests/TEST_PLAN.md §Security tests:
error envelopes, no user enumeration, cross-user isolation, admin gating,
and per-profile document scoping.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.core.config import get_settings
from app.db.models import User, UserRole


@pytest.fixture
async def api(db_session: Any) -> AsyncClient:
    from app.main import app

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        yield client


def _email(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}@example.com"


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _register(api: AsyncClient, email: str, **extra: Any) -> dict[str, Any]:
    response = await api.post(
        "/api/v1/auth/register",
        json={"email": email, "password": "correct-horse-1", **extra},
    )
    assert response.status_code == 201, response.text
    return response.json()


async def test_register_then_me_roundtrip(api: AsyncClient) -> None:
    raw_email = _email("Round.Trip")
    body = await _register(api, f"  {raw_email.upper()}  ", full_name="  Ada Lovelace  ")
    assert body["token"]
    user = body["user"]
    assert user["email"] == raw_email.lower()  # trimmed + lowercased
    assert user["full_name"] == "Ada Lovelace"
    assert user["role"] == "STUDENT"
    assert user["id"]

    me = await api.get("/api/v1/auth/me", headers=_auth(body["token"]))
    assert me.status_code == 200, me.text
    assert me.json()["user"] == user

    # Eager profile: ready immediately after registration.
    profile = await api.get("/api/v1/me/profile", headers=_auth(body["token"]))
    assert profile.status_code == 200, profile.text
    assert profile.json()["id"]


async def test_me_requires_authentication(api: AsyncClient) -> None:
    response = await api.get("/api/v1/auth/me")
    assert response.status_code == 401
    error = response.json()["error"]
    assert error["code"] == "UNAUTHENTICATED"
    assert error["request_id"]


async def test_duplicate_register_is_409_envelope(api: AsyncClient) -> None:
    email = _email("dupe")
    await _register(api, email)
    response = await api.post(
        "/api/v1/auth/register", json={"email": email.upper(), "password": "another-pass-1"}
    )
    assert response.status_code == 409
    error = response.json()["error"]
    assert error["code"] == "EMAIL_TAKEN"
    assert error["message"] == "An account with this email already exists"


async def test_login_success_and_indistinguishable_failures(api: AsyncClient) -> None:
    email = _email("login")
    created = await _register(api, email)

    ok = await api.post(
        "/api/v1/auth/login", json={"email": f"  {email.upper()} ", "password": "correct-horse-1"}
    )
    assert ok.status_code == 200, ok.text
    assert ok.json()["user"]["id"] == created["user"]["id"]
    assert ok.json()["token"]

    wrong_password = await api.post(
        "/api/v1/auth/login", json={"email": email, "password": "not-the-password"}
    )
    unknown_email = await api.post(
        "/api/v1/auth/login", json={"email": _email("nobody"), "password": "correct-horse-1"}
    )
    for response in (wrong_password, unknown_email):
        assert response.status_code == 401
        assert response.json()["error"]["code"] == "INVALID_CREDENTIALS"
    # Same message for both failures: no user enumeration.
    assert (
        wrong_password.json()["error"]["message"] == unknown_email.json()["error"]["message"]
    )


async def test_cross_user_profile_isolation(api: AsyncClient) -> None:
    token_a = (await _register(api, _email("iso-a")))["token"]
    token_b = (await _register(api, _email("iso-b")))["token"]

    patched = await api.patch(
        "/api/v1/me/profile", json={"career_goal": "A goal"}, headers=_auth(token_a)
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["career_goal"] == "A goal"

    seen_by_a = await api.get("/api/v1/me/profile", headers=_auth(token_a))
    assert seen_by_a.json()["career_goal"] == "A goal"

    seen_by_b = await api.get("/api/v1/me/profile", headers=_auth(token_b))
    assert seen_by_b.status_code == 200
    assert seen_by_b.json()["career_goal"] != "A goal"

    # Anonymous demo session must not leak A's data either.
    anonymous = await api.get("/api/v1/me/profile")
    assert anonymous.status_code == 200
    assert anonymous.json()["career_goal"] != "A goal"


async def test_admin_gating(
    api: AsyncClient, db_session: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The local demo account decides anonymous access; make sure it is ADMIN.
    from app.services.profile import DEMO_EMAIL

    demo = (
        await db_session.execute(select(User).where(User.email == DEMO_EMAIL))
    ).scalar_one_or_none()
    if demo is None:
        demo = User(email=DEMO_EMAIL, full_name="Demo Student", role=UserRole.ADMIN)
        db_session.add(demo)
    else:
        demo.role = UserRole.ADMIN
    await db_session.commit()

    student_token = (await _register(api, _email("student")))["token"]
    forbidden = await api.get("/api/v1/admin/search-usage", headers=_auth(student_token))
    assert forbidden.status_code == 403
    error = forbidden.json()["error"]
    assert error["code"] == "FORBIDDEN"
    assert error["message"] == "Administrator access required"

    anonymous = await api.get("/api/v1/admin/search-usage")
    assert anonymous.status_code == 200, anonymous.text
    assert "total_searches" in anonymous.json()

    # Emails in settings.admin_emails become ADMIN on register.
    admin_email = _email("boss")
    monkeypatch.setattr(get_settings(), "admin_emails", admin_email)
    promoted = await _register(api, admin_email)
    assert promoted["user"]["role"] == "ADMIN"
    allowed = await api.get("/api/v1/admin/search-usage", headers=_auth(promoted["token"]))
    assert allowed.status_code == 200, allowed.text


async def test_documents_scoped_to_own_profile(api: AsyncClient) -> None:
    token_a = (await _register(api, _email("doc-a")))["token"]
    token_b = (await _register(api, _email("doc-b")))["token"]

    created = await api.post(
        "/api/v1/documents",
        json={"document_type": "SOP", "notes": "A's statement"},
        headers=_auth(token_a),
    )
    assert created.status_code == 200, created.text
    doc_id = created.json()["id"]

    mine = await api.get("/api/v1/documents", headers=_auth(token_a))
    assert doc_id in {item["id"] for item in mine.json()["items"]}

    theirs = await api.get("/api/v1/documents", headers=_auth(token_b))
    assert doc_id not in {item["id"] for item in theirs.json()["items"]}

    # B cannot even learn that A's document exists (404, not 403).
    cross_patch = await api.patch(
        f"/api/v1/documents/{doc_id}", json={"status": "DONE"}, headers=_auth(token_b)
    )
    assert cross_patch.status_code == 404
    assert cross_patch.json()["error"]["code"] == "NOT_FOUND"

    # Owner patch still works.
    own_patch = await api.patch(
        f"/api/v1/documents/{doc_id}", json={"status": "DONE"}, headers=_auth(token_a)
    )
    assert own_patch.status_code == 200, own_patch.text
    assert own_patch.json()["status"] == "DONE"
