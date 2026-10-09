"""W3 handoff: sessions must not survive a password change.

Integration test against real PostgreSQL (tests/conftest.py). Proves the auth
middleware's single SELECT on users.password_changed_at end to end:

* a valid token whose ``iat`` predates ``password_changed_at`` → 401
  TOKEN_STALE;
* a token issued after the change → 200 (normal sessions unaffected);
* accounts that never changed their password keep older sessions (NULL
  password_changed_at never invalidates).
"""

from __future__ import annotations

import time
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt as pyjwt
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.core.security import JWT_ALGORITHM, jwt_secret
from app.db.models import User


@pytest.fixture
async def api(db_session: Any) -> AsyncIterator[AsyncClient]:
    from app import main as main_module
    from app.main import app

    main_module._rate_counters.clear()
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        yield client


async def _register(api: AsyncClient) -> tuple[str, str]:
    response = await api.post(
        "/api/v1/auth/register",
        json={
            "email": f"stale-{uuid.uuid4().hex[:8]}@example.com",
            "password": "correct-horse-1",
        },
    )
    assert response.status_code == 201, response.text
    return str(response.json()["token"]), str(response.json()["user"]["id"])


def _token_issued_at(user_id: str, iat: int) -> str:
    """A validly signed token with a chosen issue time (simulates token age)."""
    now = int(time.time())
    return pyjwt.encode(
        {"sub": user_id, "role": "STUDENT", "iat": iat, "exp": now + 3600},
        jwt_secret(),
        algorithm=JWT_ALGORITHM,
    )


async def _stamp_password_change(db_session: Any, user_id: str) -> None:
    user = (
        await db_session.execute(select(User).where(User.id == uuid.UUID(user_id)))
    ).scalar_one()
    # The change happened AFTER the old token (iat -1h) and BEFORE "now".
    user.password_changed_at = datetime.now(UTC) - timedelta(minutes=5)
    await db_session.commit()


async def test_password_change_invalidates_older_tokens(
    api: AsyncClient, db_session: Any
) -> None:
    fresh_token, user_id = await _register(api)
    older_token = _token_issued_at(user_id, int(time.time()) - 3600)
    await _stamp_password_change(db_session, user_id)

    stale = await api.get(
        "/api/v1/auth/me", headers={"Authorization": f"Bearer {older_token}"}
    )
    assert stale.status_code == 401, stale.text
    error = stale.json()["error"]
    assert error["code"] == "TOKEN_STALE"
    assert error["request_id"]

    # A session minted after the change keeps working (login flow unaffected).
    still_ok = await api.get(
        "/api/v1/auth/me", headers={"Authorization": f"Bearer {fresh_token}"}
    )
    assert still_ok.status_code == 200, still_ok.text
    assert still_ok.json()["user"]["id"] == user_id


async def test_accounts_that_never_changed_password_keep_their_sessions(
    api: AsyncClient,
) -> None:
    """NULL password_changed_at: even an hour-old token stays valid."""
    _fresh_token, user_id = await _register(api)
    older_token = _token_issued_at(user_id, int(time.time()) - 3600)

    response = await api.get(
        "/api/v1/auth/me", headers={"Authorization": f"Bearer {older_token}"}
    )
    assert response.status_code == 200, response.text
    assert response.json()["user"]["id"] == user_id
