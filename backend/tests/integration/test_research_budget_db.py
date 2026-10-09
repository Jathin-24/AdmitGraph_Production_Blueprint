"""P1-8: the run API rejects new runs with 429 BUDGET_EXCEEDED once today's
``search_runs`` count reaches the daily search budget.

`POST /research/plan` and `POST /research/runs` count today's rows (UTC) in
``search_runs`` with a single COUNT BEFORE creating a plan: at the budget the
request fails fast with a structured 429 and nothing is written or dispatched.
`AGRAPH_DAILY_SEARCH_BUDGET=0` (and the per-user variant, keyed on
``search_runs.user_id``) mean unlimited.

Budgets are set relatively to whatever rows earlier suites already inserted in
the shared scratch database, so these tests are order-independent. The
dispatch call is stubbed so no plan is ever executed (offline).

Integration test against real PostgreSQL (fixture: tests/conftest.py).
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, func, select

from app.core.budget import utc_day_start
from app.core.config import get_settings
from app.core.limits import reset_limiter
from app.db.models import ResearchPlan, SearchRun

ENDPOINTS = ("/api/v1/research/plan", "/api/v1/research/runs")
SEED_PREFIX = "budget-test-"


@pytest.fixture(autouse=True)
def _clean_limiter() -> Any:
    """The limiter is process-wide: leave empty counters behind (same contract
    as integration/test_research_caps_db.py)."""
    reset_limiter()
    yield
    reset_limiter()


@pytest.fixture(autouse=True)
def _default_budget(monkeypatch: pytest.MonkeyPatch) -> Any:
    """Every test states its own budget explicitly; start from the defaults."""
    settings = get_settings()
    monkeypatch.setattr(settings, "daily_search_budget", 500)
    monkeypatch.setattr(settings, "daily_search_budget_per_user", 0)
    yield


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


@pytest.fixture
async def seeded(db_session: Any) -> AsyncIterator[Any]:
    """Teardown: remove every search_runs row this file inserted."""
    yield db_session
    await db_session.execute(delete(SearchRun).where(SearchRun.query.like(f"{SEED_PREFIX}%")))
    await db_session.commit()


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


async def _today_count(db_session: Any) -> int:
    stmt = select(func.count()).select_from(SearchRun).where(SearchRun.requested_at >= utc_day_start())
    return int((await db_session.execute(stmt)).scalar_one())


async def _seed_searches(
    db_session: Any,
    count: int,
    *,
    user_id: str | None = None,
    yesterday: bool = False,
) -> None:
    """Insert `count` search_runs rows (today by default) tagged for cleanup."""
    requested_at = datetime.now(UTC) - (timedelta(days=1) if yesterday else timedelta(0))
    db_session.add_all(
        SearchRun(
            engine="google",
            query=f"{SEED_PREFIX}{uuid.uuid4().hex[:8]}",
            user_id=uuid.UUID(user_id) if user_id else None,
            requested_at=requested_at,
        )
        for _ in range(count)
    )
    await db_session.commit()


async def _start(api: AsyncClient, token: str, endpoint: str) -> Any:
    return await api.post(endpoint, json={"intake_year": 2027}, headers=_auth(token))


@pytest.mark.parametrize("endpoint", ENDPOINTS)
async def test_at_the_budget_both_run_endpoints_return_budget_exceeded(
    db_session: Any,
    seeded: Any,
    api: AsyncClient,
    dispatched: list[uuid.UUID],
    monkeypatch: pytest.MonkeyPatch,
    endpoint: str,
) -> None:
    token, _user_id = await _register(api, "budget-full")
    budget = await _today_count(db_session) + 1
    monkeypatch.setattr(get_settings(), "daily_search_budget", budget)
    await _seed_searches(db_session, 1)  # today's count now equals the budget
    before = await _plan_count(db_session)

    response = await _start(api, token, endpoint)

    assert response.status_code == 429, response.text
    error = response.json()["error"]
    assert error["code"] == "BUDGET_EXCEEDED"
    assert "Daily research budget reached" in error["message"]
    # Fail fast: nothing queued, nothing dispatched.
    assert await _plan_count(db_session) == before
    assert dispatched == []


async def test_below_the_budget_a_run_is_created_and_dispatched(
    db_session: Any,
    seeded: Any,
    api: AsyncClient,
    dispatched: list[uuid.UUID],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    token, _user_id = await _register(api, "budget-open")
    budget = await _today_count(db_session) + 5
    monkeypatch.setattr(get_settings(), "daily_search_budget", budget)
    await _seed_searches(db_session, 2)  # strictly below the budget

    response = await _start(api, token, "/api/v1/research/runs")

    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body) == {"research_plan_id", "status"}
    assert body["status"] == "QUEUED"
    assert dispatched == [uuid.UUID(body["research_plan_id"])]


async def test_zero_budget_is_unlimited(
    db_session: Any,
    seeded: Any,
    api: AsyncClient,
    dispatched: list[uuid.UUID],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    token, _user_id = await _register(api, "budget-zero")
    monkeypatch.setattr(get_settings(), "daily_search_budget", 0)
    await _seed_searches(db_session, 25)  # far past any default-sized budget

    response = await _start(api, token, "/api/v1/research/runs")

    assert response.status_code == 200, response.text
    assert len(dispatched) == 1


async def test_only_todays_rows_count_toward_the_budget(
    db_session: Any,
    seeded: Any,
    api: AsyncClient,
    dispatched: list[uuid.UUID],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    token, _user_id = await _register(api, "budget-days")
    budget = await _today_count(db_session) + 1
    monkeypatch.setattr(get_settings(), "daily_search_budget", budget)
    await _seed_searches(db_session, 3, yesterday=True)  # older rows, still under budget

    stale = await _start(api, token, "/api/v1/research/runs")
    assert stale.status_code == 200, stale.text  # yesterday's rows don't count

    await _seed_searches(db_session, 1)  # one search TODAY reaches the budget
    fresh = await _start(api, token, "/api/v1/research/runs")
    assert fresh.status_code == 429, fresh.text
    assert fresh.json()["error"]["code"] == "BUDGET_EXCEEDED"
    # Only the admitted run was dispatched (the blocked one wrote nothing).
    assert len(dispatched) == 1


async def test_per_user_budget_isolates_students(
    db_session: Any,
    seeded: Any,
    api: AsyncClient,
    dispatched: list[uuid.UUID],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    token_a, user_a = await _register(api, "budget-a")
    token_b, _user_b = await _register(api, "budget-b")
    monkeypatch.setattr(get_settings(), "daily_search_budget", 0)  # global off
    monkeypatch.setattr(get_settings(), "daily_search_budget_per_user", 2)
    await _seed_searches(db_session, 2, user_id=user_a)

    blocked = await _start(api, token_a, "/api/v1/research/runs")
    admitted = await _start(api, token_b, "/api/v1/research/runs")

    assert blocked.status_code == 429, blocked.text
    assert blocked.json()["error"]["code"] == "BUDGET_EXCEEDED"
    assert admitted.status_code == 200, admitted.text
    assert len(dispatched) == 1, "one student's spend must not starve everyone else"
