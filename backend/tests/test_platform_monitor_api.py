"""Monitor subscription scoping (TEST_PLAN §Security: cross-user access).

List, on-demand check and snapshot history are scoped by
MonitorSubscription.profile_id: another participant's subscription is
404-indistinguishable from a missing one, while the shared demo profile keeps
working for anonymous traffic.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, select

from app.db.models import MonitorSnapshot, MonitorSubscription, StudentProfile, User, UserRole


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _ensure_demo_profile(db_session: Any) -> None:
    """Seed the shared demo account if this database does not have it yet.

    Without it, `get_or_create_default_user` falls back to the oldest
    registered user for anonymous traffic — exactly the cross-user leak the
    demo profile exists to prevent.
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
async def env(db_session: Any) -> AsyncIterator[dict[str, Any]]:
    # Fresh rate-limit budget: this file posts to limited endpoints and must
    # not inherit a nearly-exhausted window from earlier suites.
    from app import main as main_module
    from app.main import app

    main_module._rate_counters.clear()

    # Ensure the shared demo account exists before anyone registers: anonymous
    # traffic must map to the demo profile, never to whichever participant
    # registered first (services/profile.py demo-account contract).
    await _ensure_demo_profile(db_session)

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

        token_a, user_a = await register("mon-a")
        token_b, user_b = await register("mon-b")
        created_subs: list[uuid.UUID] = []

        yield {
            "api": api,
            "db_session": db_session,
            "token_a": token_a,
            "token_b": token_b,
            "created_subs": created_subs,
        }

        if created_subs:
            snapshots = (
                await db_session.execute(
                    select(MonitorSnapshot).where(MonitorSnapshot.subscription_id.in_(created_subs))
                )
            ).scalars().all()
            for snapshot in snapshots:
                await db_session.delete(snapshot)
            await db_session.execute(
                delete(MonitorSubscription).where(MonitorSubscription.id.in_(created_subs))
            )
        profile_ids = []
        for user_id in (user_a, user_b):
            profile = (
                await db_session.execute(
                    select(StudentProfile).where(StudentProfile.user_id == user_id)
                )
            ).scalars().first()
            if profile is not None:
                profile_ids.append(profile.id)
        if profile_ids:
            await db_session.execute(
                delete(StudentProfile).where(StudentProfile.id.in_(profile_ids))
            )
        await db_session.execute(delete(User).where(User.id.in_([user_a, user_b])))
        await db_session.commit()
        main_module._rate_counters.clear()


async def _create_subscription(
    api: AsyncClient, headers: dict[str, str] | None = None
) -> str:
    response = await api.post(
        "/api/v1/monitor/subscriptions",
        json={"field_key": "deadline", "frequency": "WEEKLY"},
        headers=headers or {},
    )
    assert response.status_code == 200, response.text
    return response.json()["id"]


async def test_monitor_endpoints_404_cross_user(env: dict[str, Any]) -> None:
    api: AsyncClient = env["api"]
    sub_id = await _create_subscription(api, _auth(env["token_a"]))
    env["created_subs"].append(uuid.UUID(sub_id))
    cross = _auth(env["token_b"])

    # List: B never sees A's subscription.
    listing = await api.get("/api/v1/monitor/subscriptions", headers=cross)
    assert listing.status_code == 200
    assert sub_id not in {item["id"] for item in listing.json()["items"]}

    # On-demand check: enforced inside MonitoringService.run_check.
    check = await api.post(f"/api/v1/monitor/subscriptions/{sub_id}/check", headers=cross)
    assert check.status_code == 404, check.text
    assert check.json()["error"]["code"] == "NOT_FOUND"

    # Snapshot history: 404, not an empty 200 (no existence oracle).
    changes = await api.get(f"/api/v1/monitor/subscriptions/{sub_id}/changes", headers=cross)
    assert changes.status_code == 404, changes.text
    assert changes.json()["error"]["code"] == "NOT_FOUND"

    # Unknown id is indistinguishable from a foreign one.
    unknown = await api.post(
        f"/api/v1/monitor/subscriptions/{uuid.uuid4()}/check", headers=cross
    )
    assert unknown.status_code == 404

    # The owner still sees and checks it.
    owner_list = await api.get("/api/v1/monitor/subscriptions", headers=_auth(env["token_a"]))
    assert sub_id in {item["id"] for item in owner_list.json()["items"]}
    owner_check = await api.post(
        f"/api/v1/monitor/subscriptions/{sub_id}/check", headers=_auth(env["token_a"])
    )
    assert owner_check.status_code == 200, owner_check.text
    owner_changes = await api.get(
        f"/api/v1/monitor/subscriptions/{sub_id}/changes", headers=_auth(env["token_a"])
    )
    assert owner_changes.status_code == 200, owner_changes.text
    assert len(owner_changes.json()["items"]) == 1


async def test_anonymous_demo_profile_keeps_its_own_subscriptions(env: dict[str, Any]) -> None:
    api: AsyncClient = env["api"]
    demo_sub = await _create_subscription(api)  # anonymous -> shared demo profile
    env["created_subs"].append(uuid.UUID(demo_sub))

    listing = await api.get("/api/v1/monitor/subscriptions")
    assert listing.status_code == 200
    assert demo_sub in {item["id"] for item in listing.json()["items"]}

    check = await api.post(f"/api/v1/monitor/subscriptions/{demo_sub}/check")
    assert check.status_code == 200, check.text

    changes = await api.get(f"/api/v1/monitor/subscriptions/{demo_sub}/changes")
    assert changes.status_code == 200, changes.text
    assert len(changes.json()["items"]) >= 1

    # A registered participant must not reach the demo subscription.
    foreign = await api.get(
        f"/api/v1/monitor/subscriptions/{demo_sub}/changes", headers=_auth(env["token_a"])
    )
    assert foreign.status_code == 404
    foreign_check = await api.post(
        f"/api/v1/monitor/subscriptions/{demo_sub}/check", headers=_auth(env["token_a"])
    )
    assert foreign_check.status_code == 404
