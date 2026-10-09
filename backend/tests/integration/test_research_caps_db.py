"""P1-7: the run API fails fast with 429 RESOURCE_EXHAUSTED at the per-user cap.

`POST /research/plan` and `POST /research/runs` peek at the process-wide
RunLimiter (app/core/limits.py) BEFORE creating a plan: at the cap, a new run
could never be admitted before the current ones finish, so it would sit QUEUED
forever. Instead the caller gets a structured 429 with an actionable message
and no plan row is written.

The dispatch call is stubbed so no plan is ever executed here (offline).

Integration test against real PostgreSQL (fixture: tests/conftest.py).
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select

from app.core.limits import get_limiter, reset_limiter
from app.db.models import ResearchPlan

ENDPOINTS = ("/api/v1/research/plan", "/api/v1/research/runs")


@pytest.fixture(autouse=True)
def _clean_limiter() -> Any:
    """The limiter is process-wide: this file takes real slots, so it must
    start from (and leave behind) empty counters."""
    reset_limiter()
    yield
    reset_limiter()


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
async def dispatched(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[list[uuid.UUID]]:
    """Record dispatches instead of running the plan (offline, deterministic)."""
    from app.api.v1 import research as research_api

    calls: list[uuid.UUID] = []
    monkeypatch.setattr(research_api, "dispatch_plan", calls.append)
    yield calls


async def _register(api: AsyncClient, prefix: str) -> tuple[str, str]:
    response = await api.post(
        "/api/v1/auth/register",
        json={
            "email": f"{prefix}-{uuid.uuid4().hex[:8]}@example.com",
            "password": "correct-horse-1",
        },
    )
    assert response.status_code == 201, response.text
    body = response.json()
    return str(body["token"]), str(body["user"]["id"])


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _plan_count(db_session: Any) -> int:
    return int((await db_session.execute(select(func.count(ResearchPlan.id)))).scalar_one())


async def _hold_all_slots(user_id: str) -> int:
    """Take every slot the per-user cap allows; returns how many were taken."""
    limiter = get_limiter()
    for _ in range(limiter.max_per_user):
        await limiter.acquire(uuid.UUID(user_id))
    return limiter.max_per_user


async def _start(api: AsyncClient, token: str, endpoint: str) -> Any:
    return await api.post(endpoint, json={"intake_year": 2027}, headers=_auth(token))


@pytest.mark.parametrize("endpoint", ENDPOINTS)
async def test_at_the_cap_both_run_endpoints_fail_fast(
    db_session: Any, api: AsyncClient, dispatched: list[uuid.UUID], endpoint: str
) -> None:
    token, user_id = await _register(api, "cap-full")
    held = await _hold_all_slots(user_id)
    before = await _plan_count(db_session)

    response = await _start(api, token, endpoint)

    assert response.status_code == 429, response.text
    error = response.json()["error"]
    assert error["code"] == "RESOURCE_EXHAUSTED"
    assert "research runs in progress" in error["message"]
    assert str(held) in error["message"], "the message must state the actual cap"
    # Fail fast means nothing was queued: no plan row, no dispatch.
    assert await _plan_count(db_session) == before
    assert dispatched == []


async def test_below_the_cap_a_run_is_created_and_dispatched(
    db_session: Any, api: AsyncClient, dispatched: list[uuid.UUID]
) -> None:
    token, user_id = await _register(api, "cap-open")
    assert get_limiter().active_for(uuid.UUID(user_id)) == 0

    response = await _start(api, token, "/api/v1/research/runs")

    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body) == {"research_plan_id", "status"}
    assert body["status"] == "QUEUED"
    assert dispatched == [uuid.UUID(body["research_plan_id"])]
    assert await _plan_count(db_session) >= 1


async def test_the_cap_is_per_user_not_shared_between_students(
    api: AsyncClient, dispatched: list[uuid.UUID]
) -> None:
    full_token, full_user = await _register(api, "cap-one")
    other_token, _ = await _register(api, "cap-two")
    await _hold_all_slots(full_user)

    blocked = await _start(api, full_token, "/api/v1/research/runs")
    admitted = await _start(api, other_token, "/api/v1/research/runs")

    assert blocked.status_code == 429, blocked.text
    assert admitted.status_code == 200, admitted.text
    assert len(dispatched) == 1, "one student's backlog must not starve everyone else"


async def test_releasing_a_slot_admits_the_next_run(
    api: AsyncClient, dispatched: list[uuid.UUID]
) -> None:
    token, user_id = await _register(api, "cap-release")
    await _hold_all_slots(user_id)
    limiter = get_limiter()
    owner = uuid.UUID(user_id)

    blocked = await _start(api, token, "/api/v1/research/runs")
    await limiter.release(owner)
    admitted = await _start(api, token, "/api/v1/research/runs")

    assert blocked.status_code == 429, blocked.text
    assert admitted.status_code == 200, admitted.text
    assert len(dispatched) == 1
