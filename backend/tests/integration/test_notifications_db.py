"""Notification endpoints, event listeners and monitor contract — real PostgreSQL.

Goes through the ASGI app (same as the frontend) for the HTTP contracts, and
calls the listeners directly for the fire-and-forget event path. Email is
redirected to a per-test outbox so nothing leaves the machine.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.core.config import get_settings
from app.db.models import (
    Evidence,
    EvidenceStatus,
    Institution,
    Notification,
    Program,
    ResearchPlan,
    RunStatus,
    Source,
    StudentProfile,
    User,
)
from app.services.mail import mail_enabled
from app.services.notifications import (
    create_notification,
    on_research_run_finished,
    on_user_registered,
)
from app.services.profile import get_or_create_default_user


@pytest.fixture(autouse=True)
def _outbox_to_tmp(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> None:
    """Listener/check emails land in a per-test outbox instead of var/outbox."""
    monkeypatch.setattr(get_settings(), "email_outbox_dir", str(tmp_path / "outbox"))


@pytest.fixture
async def api(db_session: Any) -> AsyncClient:
    from app.main import app

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        yield client


async def _default_user(session: Any) -> User:
    """The user the HTTP endpoints act as (demo account, committed)."""
    user = await get_or_create_default_user(session)
    await session.commit()
    return user


async def _make_user_profile(session: Any, email: str) -> tuple[User, StudentProfile]:
    user = User(email=email, full_name="Listener Tester")
    session.add(user)
    await session.flush()
    profile = StudentProfile(user_id=user.id)
    session.add(profile)
    await session.flush()
    await session.commit()
    return user, profile


async def _notifications_of_type(session: Any, user_id: Any, kind: str) -> list[Notification]:
    rows = await session.execute(
        select(Notification)
        .where(Notification.user_id == user_id, Notification.type == kind)
        .order_by(Notification.created_at)
    )
    return list(rows.scalars().all())


# ------------------------------------------------------------- inbox endpoints


async def test_inbox_list_mark_read_and_read_all(api: AsyncClient, db_session: Any) -> None:
    user = await _default_user(db_session)
    n1 = await create_notification(
        db_session, user.id, "DEADLINE_CHANGED", "Deadline changed", "First body", link="/monitor"
    )
    n2 = await create_notification(
        db_session, user.id, "COST_CHANGED", "Cost changed", "Second body", link="/monitor"
    )

    r = await api.get("/api/v1/notifications")
    assert r.status_code == 200, r.text
    data = r.json()
    ids = [item["id"] for item in data["items"]]
    assert str(n1.id) in ids and str(n2.id) in ids
    assert data["unread_count"] >= 2
    assert ids.index(str(n2.id)) < ids.index(str(n1.id))  # newest first

    r = await api.post(f"/api/v1/notifications/{n2.id}/read")
    assert r.status_code == 200, r.text
    assert r.json() == {"read": True}
    r = await api.post(f"/api/v1/notifications/{n2.id}/read")  # idempotent
    assert r.status_code == 200 and r.json() == {"read": True}

    r = await api.get("/api/v1/notifications")
    marked = next(i for i in r.json()["items"] if i["id"] == str(n2.id))
    assert marked["read"] is True
    assert r.json()["unread_count"] == data["unread_count"] - 1

    r = await api.post("/api/v1/notifications/read-all")
    assert r.status_code == 200, r.text
    assert r.json()["marked"] >= 1

    r = await api.get("/api/v1/notifications")
    assert r.status_code == 200
    assert r.json()["unread_count"] == 0


async def test_read_rejects_foreign_and_missing_notifications(
    api: AsyncClient, db_session: Any
) -> None:
    await _default_user(db_session)
    other = User(email=f"foreign-{uuid.uuid4().hex[:10]}@example.com", full_name="Foreign User")
    db_session.add(other)
    await db_session.flush()
    foreign = Notification(
        user_id=other.id,
        type="WELCOME",
        title="Not yours",
        body="Another account's notification",
        link="/onboarding",
        payload={},
        email_status="SKIPPED",
    )
    db_session.add(foreign)
    await db_session.commit()

    r = await api.post(f"/api/v1/notifications/{foreign.id}/read")
    assert r.status_code == 404, r.text
    assert r.json()["error"]["code"] == "NOT_FOUND"

    r = await api.post(f"/api/v1/notifications/{uuid.uuid4()}/read")
    assert r.status_code == 404, r.text
    assert r.json()["error"]["code"] == "NOT_FOUND"


# ------------------------------------------------------------------ listeners


async def test_research_run_finished_listener(db_session: Any) -> None:
    user, profile = await _make_user_profile(db_session, "listener-research@example.com")
    plan = ResearchPlan(
        profile_id=profile.id,
        status=RunStatus.SUCCEEDED,
        requested_goal={"goal": "masters in data science"},
    )
    db_session.add(plan)
    await db_session.commit()

    done_task = asyncio.create_task(asyncio.sleep(0))
    await done_task  # finished, not cancelled, no exception -> success

    await on_research_run_finished(plan_id=str(plan.id), task=done_task)

    rows = await _notifications_of_type(db_session, user.id, "RESEARCH_COMPLETE")
    assert len(rows) == 1
    assert rows[0].link == "/research"
    assert rows[0].payload.get("plan_id") == str(plan.id)
    expected_status = "SENT" if mail_enabled(get_settings()) else "SKIPPED"
    assert rows[0].email_status == expected_status

    # malformed / unknown plan ids must never raise and must never notify
    await on_research_run_finished(plan_id="not-a-uuid", task=None)
    await on_research_run_finished(plan_id=str(uuid.uuid4()), task=None)
    rows_after = await _notifications_of_type(db_session, user.id, "RESEARCH_COMPLETE")
    assert len(rows_after) == 1

    failed_plan = ResearchPlan(
        profile_id=profile.id,
        status=RunStatus.FAILED,
        requested_goal={},
        error_message="Provider timed out",
    )
    db_session.add(failed_plan)
    await db_session.commit()
    await on_research_run_finished(plan_id=str(failed_plan.id), task=None)

    failed_rows = await _notifications_of_type(db_session, user.id, "RESEARCH_FAILED")
    assert len(failed_rows) == 1
    assert failed_rows[0].link == "/research"
    assert "Provider timed out" in failed_rows[0].body


async def test_user_registered_listener_welcomes_and_coerces(
    db_session: Any, tmp_path: Any
) -> None:
    user = User(email="new-student@example.com", full_name="New Student")
    db_session.add(user)
    await db_session.commit()

    # non-string full_name and stringified user_id must be tolerated
    await on_user_registered(user_id=str(user.id), email=user.email, full_name=123)

    rows = await _notifications_of_type(db_session, user.id, "WELCOME")
    assert len(rows) == 1
    assert rows[0].link == "/onboarding"
    assert "Hi 123" in rows[0].body  # coerced, not crashed

    if not mail_enabled(get_settings()):
        outbox = tmp_path / "outbox"
        assert outbox.exists()
        assert list(outbox.glob("*.txt"))  # welcome email written locally


# ------------------------------------------------------------ monitor contract


async def test_monitor_endpoints_expose_schedule_and_explanations(api: AsyncClient) -> None:
    r = await api.post(
        "/api/v1/monitor/subscriptions",
        json={"field_key": "deadline", "frequency": "WEEKLY"},
    )
    assert r.status_code == 200, r.text
    sub_id = r.json()["id"]

    r = await api.get("/api/v1/monitor/subscriptions")
    assert r.status_code == 200
    item = next(i for i in r.json()["items"] if i["id"] == sub_id)
    assert item["next_check_at"] is not None  # set on creation (now + 7d)
    assert "last_checked_at" in item
    assert item["last_checked_at"] is None

    r = await api.post(f"/api/v1/monitor/subscriptions/{sub_id}/check")
    assert r.status_code == 200, r.text
    checked = r.json()
    assert checked["change_type"] == "deadline"
    assert checked["material_change"] is False
    assert isinstance(checked["explanation"], str)  # the check endpoint always explains
    assert "old_value" in checked and "new_value" in checked

    r = await api.get(f"/api/v1/monitor/subscriptions/{sub_id}/changes")
    assert r.status_code == 200, r.text
    items = r.json()["items"]
    assert len(items) >= 1
    assert {"old_value", "new_value", "explanation"} <= set(items[0])
    assert items[0]["explanation"] is None  # immaterial rows read as null in history

    r = await api.get("/api/v1/monitor/subscriptions")
    item = next(i for i in r.json()["items"] if i["id"] == sub_id)
    assert item["last_checked_at"] is not None  # advanced by the check

    r = await api.post(
        "/api/v1/monitor/subscriptions", json={"field_key": "nonsense", "frequency": "WEEKLY"}
    )
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "VALIDATION_ERROR"

    r = await api.post(
        "/api/v1/monitor/subscriptions", json={"field_key": "deadline", "frequency": "HOURLY"}
    )
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "VALIDATION_ERROR"


async def test_check_detects_material_change_and_notifies(api: AsyncClient, db_session: Any) -> None:
    # Program-scoped evidence: only this test's stored rows are considered.
    institution = Institution(
        canonical_name="Monitor Material University",
        normalized_name="monitor material university",
        domain="material.example.edu",
    )
    db_session.add(institution)
    await db_session.flush()
    program = Program(
        institution_id=institution.id,
        canonical_name="M.Sc. Material Watch",
        normalized_name="m.sc. material watch",
    )
    db_session.add(program)
    await db_session.flush()
    source = Source(
        url="https://material.example.edu/msc",
        canonical_url="https://material.example.edu/msc",
        domain="material.example.edu",
        title="Programme page",
    )
    db_session.add(source)
    await db_session.flush()
    evidence = Evidence(
        source_id=source.id,
        claim_type="deadline",
        subject_type="program",
        subject_id=program.id,
        claim="Applications close 15 Jan 2027",
        normalized_claim="application_deadline",
        extracted_value={"date": "2027-01-15"},
        retrieved_at=datetime.now(UTC) - timedelta(days=2),
        status=EvidenceStatus.CURRENT,
    )
    db_session.add(evidence)
    await db_session.commit()

    r = await api.post(
        "/api/v1/monitor/subscriptions",
        json={"field_key": "deadline", "frequency": "DAILY", "program_id": str(program.id)},
    )
    assert r.status_code == 200, r.text
    sub_id = r.json()["id"]

    r = await api.post(f"/api/v1/monitor/subscriptions/{sub_id}/check")
    assert r.status_code == 200, r.text
    first = r.json()
    assert first["material_change"] is False  # nothing to compare against yet
    assert first["new_value"] == "2027-01-15"
    assert isinstance(first["explanation"], str)

    # the observed value moves (old-stored vs new-observed, both real rows)
    evidence.extracted_value = {"date": "2027-02-01"}
    evidence.retrieved_at = datetime.now(UTC) - timedelta(days=1)
    await db_session.commit()

    r = await api.post(f"/api/v1/monitor/subscriptions/{sub_id}/check")
    assert r.status_code == 200, r.text
    second = r.json()
    assert second["material_change"] is True
    assert second["old_value"] == "2027-01-15"
    assert second["new_value"] == "2027-02-01"
    assert second["explanation"] == (
        "Deadline moved from 15 Jan 2027 to 1 Feb 2027 (based on last stored evidence)"
    )

    rows = (
        (
            await db_session.execute(
                select(Notification).where(Notification.type == "DEADLINE_CHANGED")
            )
        )
        .scalars()
        .all()
    )
    matches = [n for n in rows if (n.payload or {}).get("snapshot_id") == second["id"]]
    assert len(matches) == 1
    assert matches[0].link == "/monitor"
    assert "Deadline moved from 15 Jan 2027 to 1 Feb 2027" in matches[0].body

    r = await api.get(f"/api/v1/monitor/subscriptions/{sub_id}/changes")
    assert r.status_code == 200, r.text
    items = r.json()["items"]
    assert len(items) == 2
    newest = items[0]
    assert newest["material_change"] is True
    assert newest["explanation"] == second["explanation"]
    assert items[1]["material_change"] is False
    assert items[1]["explanation"] is None
    assert items[1]["old_value"] is None
