"""POST /research/plan dispatch + run-scoped authorization.

Covers API_CONTRACT §Research (the plan endpoint must actually run the plan,
with Idempotency-Key semantics matching /research/runs) and TEST_PLAN §Security
("authorization prevents cross-user profile access") for every run endpoint.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, select

from app.db.models import ResearchPlan, StudentProfile, User, UserRole


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _ensure_demo_profile(db_session: Any) -> None:
    """Seed the shared demo account if this database does not have it yet.

    Without it, `get_or_create_default_user` falls back to the oldest
    registered user for anonymous traffic — exactly the cross-user leak the
    demo profile exists to prevent. Production seeds it the same way (first
    anonymous request on a fresh database, or deployment/ENVIRONMENT.md).
    """
    from app.services.profile import DEMO_EMAIL, get_or_create_profile

    existing = (
        await db_session.execute(select(User).where(User.email == DEMO_EMAIL))
    ).scalar_one_or_none()
    if existing is None:
        db_session.add(User(email=DEMO_EMAIL, full_name="Demo Student", role=UserRole.ADMIN))
    await get_or_create_profile(db_session)
    await db_session.commit()


@pytest.fixture
async def env(db_session: Any, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[dict[str, Any]]:
    """Two registered participants, a dispatch-recording monkeypatch, teardown."""
    from app import main as main_module
    from app.api.v1 import research as research_api
    from app.main import app

    # Fresh rate-limit budget: this file posts to limited endpoints and must
    # not inherit a nearly-exhausted window from earlier suites.
    main_module._rate_counters.clear()

    # Ensure the shared demo account exists before anyone registers: anonymous
    # traffic must map to the demo profile, never to whichever participant
    # registered first (services/profile.py demo-account contract).
    await _ensure_demo_profile(db_session)

    dispatched: list[uuid.UUID] = []
    monkeypatch.setattr(research_api, "dispatch_plan", lambda plan_id: dispatched.append(plan_id))

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as api:

        async def register(prefix: str) -> tuple[str, uuid.UUID]:
            email = f"{prefix}-{uuid.uuid4().hex[:8]}@example.com"
            response = await api.post(
                "/api/v1/auth/register", json={"email": email, "password": "correct-horse-1"}
            )
            assert response.status_code == 201, response.text
            body = response.json()
            return body["token"], uuid.UUID(body["user"]["id"])

        token_a, user_a = await register("plan-a")
        token_b, user_b = await register("plan-b")
        created_plans: list[uuid.UUID] = []

        yield {
            "api": api,
            "db_session": db_session,
            "dispatched": dispatched,
            "token_a": token_a,
            "token_b": token_b,
            "created_plans": created_plans,
        }

    # Wipe rows these tests created (steps cascade from plans, profiles from
    # users the same way integration/test_demo_db tears down).
    profile_ids = []
    for user_id in (user_a, user_b):
        profile = (
            await db_session.execute(select(StudentProfile).where(StudentProfile.user_id == user_id))
        ).scalars().first()
        if profile is not None:
            profile_ids.append(profile.id)
    if created_plans:
        await db_session.execute(delete(ResearchPlan).where(ResearchPlan.id.in_(created_plans)))
    if profile_ids:
        await db_session.execute(delete(ResearchPlan).where(ResearchPlan.profile_id.in_(profile_ids)))
        await db_session.execute(delete(StudentProfile).where(StudentProfile.id.in_(profile_ids)))
    await db_session.execute(delete(User).where(User.id.in_([user_a, user_b])))
    await db_session.commit()
    main_module._rate_counters.clear()


async def test_create_plan_dispatches_the_queued_run(env: dict[str, Any]) -> None:
    api: AsyncClient = env["api"]
    response = await api.post(
        "/api/v1/research/plan",
        json={"goal": {"country": "DE", "degree": "masters"}},
        headers=_auth(env["token_a"]),
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body) == {"research_plan_id", "status"}
    assert body["status"] == "QUEUED"
    plan_id = uuid.UUID(body["research_plan_id"])
    env["created_plans"].append(plan_id)
    # POST /research/plan now dispatches exactly like POST /research/runs.
    assert env["dispatched"] == [plan_id]


async def test_create_plan_honours_idempotency_key(env: dict[str, Any]) -> None:
    api: AsyncClient = env["api"]
    headers = {**_auth(env["token_a"]), "Idempotency-Key": "idem-plan-key-1"}
    payload = {"goal": {"country": "NL"}}

    first = await api.post("/api/v1/research/plan", json=payload, headers=headers)
    assert first.status_code == 200, first.text
    first_id = first.json()["research_plan_id"]
    env["created_plans"].append(first_id)

    # Same key + same user -> the SAME run, never a second one.
    replay = await api.post("/api/v1/research/plan", json=payload, headers=headers)
    assert replay.status_code == 200, replay.text
    assert replay.json()["research_plan_id"] == first_id

    # A different key is a different run (same semantics as /research/runs).
    other = await api.post(
        "/api/v1/research/plan",
        json=payload,
        headers={**_auth(env["token_a"]), "Idempotency-Key": "idem-plan-key-2"},
    )
    assert other.status_code == 200, other.text
    other_id = other.json()["research_plan_id"]
    env["created_plans"].append(other_id)
    assert other_id != first_id

    # Another user with the same key gets their own run (key is profile-scoped).
    theirs = await api.post(
        "/api/v1/research/plan",
        json=payload,
        headers={**_auth(env["token_b"]), "Idempotency-Key": "idem-plan-key-1"},
    )
    assert theirs.status_code == 200, theirs.text
    theirs_id = theirs.json()["research_plan_id"]
    env["created_plans"].append(theirs_id)
    assert theirs_id not in (first_id, other_id)


async def test_run_endpoints_404_for_cross_user_and_anonymous(env: dict[str, Any]) -> None:
    api: AsyncClient = env["api"]
    response = await api.post(
        "/api/v1/research/plan", json={"goal": {}}, headers=_auth(env["token_a"])
    )
    assert response.status_code == 200, response.text
    plan_id = response.json()["research_plan_id"]
    env["created_plans"].append(plan_id)

    cross_headers = _auth(env["token_b"])
    probes = [
        ("GET", f"/api/v1/research/runs/{plan_id}"),
        ("GET", f"/api/v1/research/runs/{plan_id}/events"),
        ("POST", f"/api/v1/research/runs/{plan_id}/cancel"),
    ]
    for method, path in probes:
        # Participant B must not learn that A's run exists (404, not 403).
        denied = await api.request(method, path, headers=cross_headers)
        assert denied.status_code == 404, f"{method} {path}: {denied.text}"
        assert denied.json()["error"]["code"] == "NOT_FOUND"
        # Anonymous/demo sessions are a different profile as well.
        anonymous = await api.request(method, path)
        assert anonymous.status_code == 404, f"anonymous {method} {path}: {anonymous.text}"

    # The owner keeps full access.
    owner = await api.get(f"/api/v1/research/runs/{plan_id}", headers=_auth(env["token_a"]))
    assert owner.status_code == 200, owner.text
    assert owner.json()["status"] == "QUEUED"
    events = await api.get(f"/api/v1/research/runs/{plan_id}/events", headers=_auth(env["token_a"]))
    assert events.status_code == 200, events.text
    assert len(events.json()["steps"]) == 9

    # Cross-user cancel must not change anything either.
    still_queued = await api.get(f"/api/v1/research/runs/{plan_id}", headers=_auth(env["token_a"]))
    assert still_queued.json()["status"] == "QUEUED"

    cancelled = await api.post(
        f"/api/v1/research/runs/{plan_id}/cancel", headers=_auth(env["token_a"])
    )
    assert cancelled.status_code == 200, cancelled.text
    assert cancelled.json()["status"] == "CANCELLED"


async def test_anonymous_demo_flow_keeps_working(env: dict[str, Any]) -> None:
    """Anonymous traffic uses the shared demo profile end-to-end."""
    api: AsyncClient = env["api"]
    response = await api.post("/api/v1/research/plan", json={"goal": {"country": "FR"}})
    assert response.status_code == 200, response.text
    plan_id = response.json()["research_plan_id"]
    env["created_plans"].append(plan_id)
    assert env["dispatched"] == [uuid.UUID(plan_id)]

    detail = await api.get(f"/api/v1/research/runs/{plan_id}")
    assert detail.status_code == 200, detail.text
    assert detail.json()["status"] == "QUEUED"

    events = await api.get(f"/api/v1/research/runs/{plan_id}/events")
    assert events.status_code == 200, events.text
    assert len(events.json()["steps"]) == 9

    # ...while a registered participant still cannot see the demo run.
    foreign = await api.get(f"/api/v1/research/runs/{plan_id}", headers=_auth(env["token_b"]))
    assert foreign.status_code == 404
