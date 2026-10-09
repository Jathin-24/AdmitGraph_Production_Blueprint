"""Cross-replica advisory locks for the scheduler and startup recovery (audit P1-7).

Each test simulates "another replica" by taking the same advisory key on its
own dedicated connection: a concurrent tick/recovery must then skip instead
of doing the work twice, and must run normally once the lock is released.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import delete, text

from app.db.models import ResearchPlan, RunStatus, StudentProfile, User
from app.db.session import get_engine
from app.workers import locks, recovery, scheduler

_COUNTER_KEYS = {
    "checks_run",
    "checks_failed",
    "stale_marked",
    "stale_notifications",
    "reminders_sent",
    "notifications_created",
}


async def test_run_tick_skips_while_another_replica_holds_the_lock(monkeypatch: Any, db_session: Any) -> None:
    # Never let a lifespan background-tick (from another test's TestClient)
    # race these assertions: the scheduler stays stopped for this test only.
    await scheduler.stop()

    calls: list[str] = []

    async def fake_checks(*args: Any) -> None:
        calls.append("checks")

    async def fake_sweep(*args: Any) -> None:
        calls.append("sweep")

    async def fake_reminders(*args: Any) -> None:
        calls.append("reminders")

    monkeypatch.setattr(scheduler, "_run_due_checks", fake_checks)
    monkeypatch.setattr(scheduler, "_freshness_sweep", fake_sweep)
    monkeypatch.setattr(scheduler, "_roadmap_reminders", fake_reminders)

    engine = get_engine()
    before = len(calls)
    async with engine.connect() as other_replica:
        await other_replica.execute(
            text("SELECT pg_advisory_lock(:key)"), {"key": locks.SCHEDULER_TICK_LOCK}
        )
        summary = await scheduler.run_tick()
        # Release like a real replica shutting down: `connect()` pools the
        # connection instead of closing the server session, and the session-
        # level advisory lock would otherwise survive into the next assertion.
        await other_replica.execute(
            text("SELECT pg_advisory_unlock(:key)"), {"key": locks.SCHEDULER_TICK_LOCK}
        )

    # Concurrent tick: cheap skip, zero work, all counters untouched.
    assert summary.keys() == _COUNTER_KEYS
    assert all(value == 0 for value in summary.values())
    assert len(calls) == before, "a tick ran while another replica held the lock"

    # Lock released with the other "replica's" connection -> the next tick runs.
    summary = await scheduler.run_tick()
    assert {"checks", "sweep", "reminders"} <= set(calls[before:])
    assert summary.keys() == _COUNTER_KEYS


async def test_recovery_skips_claiming_while_another_replica_holds_the_lock(
    monkeypatch: Any, db_session: Any
) -> None:
    user = User(email=f"lock-recovery-{uuid.uuid4().hex[:8]}@test.local", full_name="Lock Test")
    db_session.add(user)
    await db_session.flush()
    profile = StudentProfile(user_id=user.id)
    db_session.add(profile)
    await db_session.flush()
    stale_queued = ResearchPlan(
        profile_id=profile.id,
        status=RunStatus.QUEUED,
        requested_goal={"goal": "stale under lock"},
        created_at=datetime.now(UTC) - timedelta(seconds=recovery.QUEUED_STALE_SECONDS + 60),
    )
    db_session.add(stale_queued)
    await db_session.commit()
    plan_id, user_id, profile_id = stale_queued.id, user.id, profile.id

    dispatched: list[uuid.UUID] = []
    from app.services.research import orchestrator

    monkeypatch.setattr(orchestrator, "dispatch_plan", lambda pid: dispatched.append(pid))

    engine = get_engine()
    try:
        async with engine.connect() as other_replica:
            await other_replica.execute(
                text("SELECT pg_advisory_lock(:key)"), {"key": locks.RECOVERY_LOCK}
            )
            # Skip: another replica is recovering, so this one must do nothing.
            assert await recovery.recover_research_plans() == (0, 0)
            db_session.expire_all()
            claim_attempt = await db_session.get(ResearchPlan, plan_id)
            # End the read transaction before recovery tries to UPDATE: an
            # idle-in-transaction SELECT would block its claim forever.
            await db_session.commit()
            assert claim_attempt is not None
            assert claim_attempt.status == RunStatus.QUEUED, "skip must not claim rows"
            assert dispatched == []
            # Release like a real replica shutting down (the pooled connection
            # would otherwise keep holding the session-level lock).
            await other_replica.execute(
                text("SELECT pg_advisory_unlock(:key)"), {"key": locks.RECOVERY_LOCK}
            )

        # Lock released -> recovery runs and claims the stale plan exactly once.
        requeued, _failed = await recovery.recover_research_plans()
        assert requeued >= 1
        db_session.expire_all()
        claimed = await db_session.get(ResearchPlan, plan_id)
        assert claimed is not None
        assert claimed.status == RunStatus.RUNNING
        assert plan_id in dispatched
    finally:
        # profile/user rows are expired by expire_all(); use captured ids.
        await db_session.execute(delete(ResearchPlan).where(ResearchPlan.profile_id == profile_id))
        await db_session.execute(delete(StudentProfile).where(StudentProfile.id == profile_id))
        await db_session.execute(delete(User).where(User.id == user_id))
        await db_session.commit()
