"""In-process scheduler: tick phases (due checks, freshness, reminders) + lifecycle.

DB-backed tests run against the scratch PostgreSQL (tests/conftest.py); the
lifecycle tests only exercise start/stop/is_running with a long tick interval
so no background work races the assertions.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import select

from app.core.config import get_settings
from app.db.models import (
    Evidence,
    EvidenceStatus,
    Institution,
    MonitorSnapshot,
    MonitorSubscription,
    Notification,
    Program,
    RoadmapTask,
    RunStatus,
    Source,
    StrategyRun,
    StudentProfile,
    TaskStatus,
    User,
)
from app.workers import scheduler


@pytest.fixture(autouse=True)
def _outbox_to_tmp(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> None:
    """Reminders/notifications may email; keep the outbox inside the test tmp dir."""
    monkeypatch.setattr(get_settings(), "email_outbox_dir", str(tmp_path / "outbox"))


async def _make_profile(session: Any, email: str) -> tuple[User, StudentProfile]:
    user = User(email=email, full_name="Scheduler Tester")
    session.add(user)
    await session.flush()
    profile = StudentProfile(user_id=user.id)
    session.add(profile)
    await session.flush()
    await session.commit()
    return user, profile


async def _make_program(session: Any, slug: str) -> Program:
    institution = Institution(
        canonical_name=f"Scheduler University {slug}",
        normalized_name=f"scheduler university {slug.lower()}",
        domain=f"{slug.lower()}.scheduler.example.edu",
    )
    session.add(institution)
    await session.flush()
    program = Program(
        institution_id=institution.id,
        canonical_name=f"M.Sc. Scheduler {slug}",
        normalized_name=f"m.sc. scheduler {slug.lower()}",
    )
    session.add(program)
    await session.commit()
    return program


async def _notifications_of_type(session: Any, user_id: Any, kind: str) -> list[Notification]:
    rows = await session.execute(
        select(Notification)
        .where(Notification.user_id == user_id, Notification.type == kind)
        .order_by(Notification.created_at)
    )
    return list(rows.scalars().all())


# ------------------------------------------------------------------ tick work


async def test_run_tick_checks_due_subscription_and_advances_schedule(
    db_session: Any,
) -> None:
    _user, profile = await _make_profile(db_session, "sched-due@example.com")
    sub = MonitorSubscription(
        profile_id=profile.id,
        field_key="deadline",
        frequency="DAILY",
        enabled=True,
        # Oldest possible due date: wins the by-next_check_at ordering even if
        # another test in this session left a subscription due as well.
        next_check_at=datetime.now(UTC) - timedelta(days=365),
    )
    db_session.add(sub)
    await db_session.commit()

    summary = await scheduler.run_tick()

    assert summary["checks_run"] >= 1
    await db_session.refresh(sub)
    assert sub.last_checked_at is not None
    assert sub.next_check_at is not None
    assert sub.next_check_at > datetime.now(UTC)  # advanced by the frequency delta
    snapshots = (
        (
            await db_session.execute(
                select(MonitorSnapshot).where(MonitorSnapshot.subscription_id == sub.id)
            )
        )
        .scalars()
        .all()
    )
    assert len(snapshots) >= 1
    assert snapshots[0].change_type == "deadline"


async def test_freshness_sweep_marks_stale_and_dedupes_source_alerts(
    db_session: Any,
) -> None:
    user, profile = await _make_profile(db_session, "sched-stale@example.com")
    program = await _make_program(db_session, "StaleWatch")
    now = datetime.now(UTC)
    source = Source(
        url="https://stalewatch.scheduler.example.edu/admissions",
        canonical_url="https://stalewatch.scheduler.example.edu/admissions",
        domain="stalewatch.scheduler.example.edu",
        title="Admissions page",
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
        retrieved_at=now - timedelta(days=10),
        freshness_deadline=now - timedelta(days=2),
        status=EvidenceStatus.CURRENT,
    )
    subscription = MonitorSubscription(
        profile_id=profile.id,
        program_id=program.id,
        field_key="deadline",
        frequency="WEEKLY",
        enabled=True,
        next_check_at=now + timedelta(days=7),  # not due: isolates the sweep phase
    )
    db_session.add_all([evidence, subscription])
    await db_session.commit()

    first = await scheduler.run_tick()
    assert first["stale_marked"] >= 1
    assert first["stale_notifications"] >= 1

    await db_session.refresh(evidence)
    assert evidence.status == EvidenceStatus.STALE
    alerts = await _notifications_of_type(db_session, user.id, "SOURCE_STALE")
    assert len(alerts) == 1
    assert alerts[0].link == "/monitor"
    assert alerts[0].payload.get("program_id") == str(program.id)

    second = await scheduler.run_tick()
    assert second["stale_notifications"] == 0
    alerts_after = await _notifications_of_type(db_session, user.id, "SOURCE_STALE")
    assert len(alerts_after) == 1  # 7-day dedupe: one alert per program per week


async def test_roadmap_reminder_is_sent_once_per_task(db_session: Any) -> None:
    user, profile = await _make_profile(db_session, "sched-remind@example.com")
    run = StrategyRun(
        profile_id=profile.id,
        scoring_version="v1",
        strategy_version="v1",
        status=RunStatus.SUCCEEDED,
    )
    db_session.add(run)
    await db_session.flush()
    task = RoadmapTask(
        strategy_run_id=run.id,
        profile_id=profile.id,
        title="Draft SoP",
        task_type="essay",
        due_date=(datetime.now(UTC) + timedelta(days=1)).date(),
        status=TaskStatus.TODO,
    )
    db_session.add(task)
    await db_session.commit()

    first = await scheduler.run_tick()
    assert first["reminders_sent"] >= 1

    reminders = await _notifications_of_type(db_session, user.id, "ROADMAP_DUE")
    assert len(reminders) == 1
    assert reminders[0].link == "/dashboard"
    assert reminders[0].payload.get("task_id") == str(task.id)
    assert "Draft SoP" in reminders[0].body
    assert reminders[0].email_status in ("SKIPPED", "SENT", "FAILED")

    await scheduler.run_tick()
    reminders_after = await _notifications_of_type(db_session, user.id, "ROADMAP_DUE")
    assert len(reminders_after) == 1  # deduped forever per task


async def test_run_tick_summary_has_every_counter_and_skips_on_overlap() -> None:
    # A tick while the previous one is still running must be a cheap no-op.
    assert scheduler._tick_in_progress is False
    scheduler._tick_in_progress = True
    try:
        summary = await scheduler.run_tick()
    finally:
        scheduler._tick_in_progress = False
    assert set(summary) == {
        "checks_run",
        "checks_failed",
        "stale_marked",
        "stale_notifications",
        "reminders_sent",
        "notifications_created",
    }
    assert all(value == 0 for value in summary.values())


# ------------------------------------------------------------------ lifecycle


async def test_start_is_a_noop_when_scheduler_is_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    await scheduler.stop()  # never leave a task behind from another test
    monkeypatch.setattr(get_settings(), "scheduler_enabled", False)

    await scheduler.start()

    assert scheduler.is_running() is False
    await scheduler.stop()  # still safe to call


async def test_start_stop_cycle_and_idempotency(monkeypatch: pytest.MonkeyPatch) -> None:
    await scheduler.stop()
    monkeypatch.setattr(get_settings(), "scheduler_enabled", True)
    monkeypatch.setattr(get_settings(), "scheduler_tick_seconds", 3600)  # no background ticks

    await scheduler.start()
    assert scheduler.is_running() is True
    await scheduler.start()  # second start must not spawn a second loop
    assert scheduler.is_running() is True

    await scheduler.stop()
    assert scheduler.is_running() is False
    await scheduler.stop()  # idempotent
    assert scheduler.is_running() is False
