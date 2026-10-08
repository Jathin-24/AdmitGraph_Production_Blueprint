"""Startup recovery for research runs interrupted by a restart (MASTER_SPEC §19).

"Idempotent research runs": stale QUEUED plans are re-dispatched exactly once,
RUNNING plans past the timeout fail with "interrupted by restart", and a
second recovery pass changes nothing.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import delete

from app.db.models import ResearchPlan, RunStatus, StudentProfile, User
from app.workers import recovery


@pytest.fixture
async def plans(db_session: Any) -> AsyncIterator[dict[str, Any]]:
    """A dedicated profile with one plan per recovery branch, then teardown."""
    user = User(email=f"recovery-{uuid.uuid4().hex[:8]}@test.local", full_name="Recovery Test")
    db_session.add(user)
    await db_session.flush()
    profile = StudentProfile(user_id=user.id)
    db_session.add(profile)
    await db_session.flush()
    user_id = user.id
    profile_id = profile.id

    now = datetime.now(UTC)
    queued_stale = timedelta(seconds=recovery.QUEUED_STALE_SECONDS + 60)
    running_stale = timedelta(seconds=recovery.RUNNING_STALE_SECONDS + 60)

    stale_queued = ResearchPlan(
        profile_id=profile_id,
        status=RunStatus.QUEUED,
        requested_goal={"goal": "stale queued"},
        created_at=now - queued_stale,
    )
    stale_running = ResearchPlan(
        profile_id=profile_id,
        status=RunStatus.RUNNING,
        requested_goal={"goal": "stale running"},
        created_at=now - running_stale,
        started_at=now - running_stale,
    )
    fresh_queued = ResearchPlan(
        profile_id=profile_id,
        status=RunStatus.QUEUED,
        requested_goal={"goal": "fresh queued"},
        created_at=now - timedelta(seconds=10),
    )
    recent_running = ResearchPlan(
        profile_id=profile_id,
        status=RunStatus.RUNNING,
        requested_goal={"goal": "recent running"},
        created_at=now - timedelta(minutes=5),
        started_at=now - timedelta(minutes=5),
    )
    terminal = ResearchPlan(
        profile_id=profile_id,
        status=RunStatus.SUCCEEDED,
        requested_goal={"goal": "already done"},
        created_at=now - running_stale,
        completed_at=now - running_stale,
    )
    db_session.add_all([stale_queued, stale_running, fresh_queued, recent_running, terminal])
    await db_session.commit()
    ids = {
        "stale_queued": stale_queued.id,
        "stale_running": stale_running.id,
        "fresh_queued": fresh_queued.id,
        "recent_running": recent_running.id,
        "terminal": terminal.id,
    }

    yield {"session": db_session, "profile_id": profile_id, "ids": ids}

    # ResearchPlanStep cascades from the plan (FK ondelete CASCADE).
    await db_session.execute(delete(ResearchPlan).where(ResearchPlan.profile_id == profile_id))
    await db_session.execute(delete(StudentProfile).where(StudentProfile.id == profile_id))
    await db_session.execute(delete(User).where(User.id == user_id))
    await db_session.commit()


async def _reloaded(plans: dict[str, Any], key: str) -> ResearchPlan:
    """Reload one plan through the test session (recovery used another one)."""
    session = plans["session"]
    session.expire_all()
    plan = await session.get(ResearchPlan, plans["ids"][key])
    assert plan is not None
    return plan


async def test_recovery_requeues_stale_queued_and_fails_interrupted_runs(
    plans: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.services.research import orchestrator

    dispatched: list[uuid.UUID] = []
    monkeypatch.setattr(orchestrator, "dispatch_plan", lambda plan_id: dispatched.append(plan_id))

    requeued, failed = await recovery.recover_research_plans()
    # Counts are >= 1: other suites may leave stale rows in the shared scratch
    # database, but this test's rows must always be part of the outcome.
    assert requeued >= 1
    assert failed >= 1

    # Stale QUEUED -> claimed (RUNNING) before dispatch, so a second pass
    # cannot hand the same plan to a worker twice.
    assert plans["ids"]["stale_queued"] in dispatched
    claimed = await _reloaded(plans, "stale_queued")
    assert claimed.status == RunStatus.RUNNING
    assert claimed.started_at is not None

    # Stale RUNNING -> FAILED, honestly labelled and closed out.
    interrupted = await _reloaded(plans, "stale_running")
    assert interrupted.status == RunStatus.FAILED
    assert interrupted.error_message == recovery.INTERRUPTED_MESSAGE
    assert interrupted.completed_at is not None

    # Everything else is untouched.
    assert plans["ids"]["fresh_queued"] not in dispatched
    fresh = await _reloaded(plans, "fresh_queued")
    assert fresh.status == RunStatus.QUEUED
    assert plans["ids"]["recent_running"] not in dispatched
    recent = await _reloaded(plans, "recent_running")
    assert recent.status == RunStatus.RUNNING
    done = await _reloaded(plans, "terminal")
    assert done.status == RunStatus.SUCCEEDED

    # Idempotent: running the recovery again changes nothing.
    dispatched.clear()
    assert await recovery.recover_research_plans() == (0, 0)
    assert dispatched == []


async def test_recovery_leaves_fresh_and_terminal_rows_untouched(
    plans: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.services.research import orchestrator

    dispatched: list[uuid.UUID] = []
    monkeypatch.setattr(orchestrator, "dispatch_plan", lambda plan_id: dispatched.append(plan_id))

    await recovery.recover_research_plans()

    assert plans["ids"]["fresh_queued"] not in dispatched
    assert plans["ids"]["recent_running"] not in dispatched
    assert plans["ids"]["terminal"] not in dispatched
    fresh = await _reloaded(plans, "fresh_queued")
    assert fresh.status == RunStatus.QUEUED
    recent = await _reloaded(plans, "recent_running")
    assert recent.status == RunStatus.RUNNING
    done = await _reloaded(plans, "terminal")
    assert done.status == RunStatus.SUCCEEDED
    assert done.error_message is None
