"""In-process async scheduler started from the FastAPI lifespan.

Workstream contract:
    await start()  — begin the tick loop (no-op when scheduler_enabled=False)
    await stop()   — cancel the loop and await it (must return promptly)

Each tick (every ``scheduler_tick_seconds``) does bounded work with
try/except-per-item so a single failure never kills the loop:

* due monitor subscriptions (``next_check_at <= now``, capped at
  ``monitor_max_checks_per_tick`` — ``MonitoringService.run_check`` advances
  the schedule itself),
* a freshness sweep (evidence past ``freshness_deadline`` becomes STALE, and
  affected programs with an active subscription get a deduped SOURCE_STALE
  notification),
* roadmap reminders for tasks due within 48h (deduped per task forever).

``run_tick()`` is public so tests can await a single tick directly.

Replica safety (audit P1-7): each tick holds a PostgreSQL advisory lock
(`app.workers.locks`) for its whole duration, so only ONE replica/uvicorn
worker ever does the work — the others skip cheaply. The process-local
``_tick_in_progress`` flag remains as the first line of defence inside a
single process (and for DB-less unit tests).
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings, get_settings
from app.db.models import (
    Evidence,
    EvidenceStatus,
    MonitorSubscription,
    Program,
    RoadmapTask,
    StudentProfile,
    TaskStatus,
)
from app.db.session import get_engine
from app.services.monitoring.service import MonitoringService
from app.services.notifications import create_notification, has_recent_notification
from app.workers.locks import SCHEDULER_TICK_LOCK, try_advisory_lock

log = logging.getLogger(__name__)

_SUMMARY_KEYS = (
    "checks_run",
    "checks_failed",
    "stale_marked",
    "stale_notifications",
    "reminders_sent",
    "notifications_created",
)
_REMINDER_HORIZON = timedelta(hours=48)
_FRESHNESS_BATCH_LIMIT = 500
_REMINDER_BATCH_LIMIT = 20

_task: asyncio.Task[None] | None = None
_tick_in_progress = False


async def start() -> None:
    """Begin background scheduling (called from app lifespan)."""
    global _task
    settings = get_settings()
    if not settings.scheduler_enabled:
        log.info("scheduler disabled (scheduler_enabled=false)")
        return
    if _task is not None and not _task.done():
        return
    _task = asyncio.create_task(_loop())
    log.info("scheduler started (tick every %ss)", settings.scheduler_tick_seconds)


async def stop() -> None:
    """Stop background scheduling and wait for the current tick (lifespan exit)."""
    global _task
    task, _task = _task, None
    if task is None:
        return
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    except Exception:  # noqa: BLE001 - shutdown must never surface worker errors
        log.exception("scheduler stopped with an error")


def is_running() -> bool:
    """Introspection helper for tests/health."""
    return _task is not None and not _task.done()


async def _loop() -> None:
    while True:
        settings = get_settings()
        await asyncio.sleep(max(1, settings.scheduler_tick_seconds))
        try:
            await run_tick()
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - one bad tick must not kill the loop
            log.exception("scheduler tick failed")


async def run_tick() -> dict[str, int]:
    """Run one bounded tick; returns a summary (also used directly by tests).

    Skips (all-zero summary) when either this process is already ticking or
    another replica holds the cross-process advisory lock — a second
    concurrent tick must never duplicate the work (audit P1-7).
    """
    global _tick_in_progress
    summary = dict.fromkeys(_SUMMARY_KEYS, 0)
    if _tick_in_progress:
        log.info("scheduler tick skipped: previous tick still running")
        return summary
    _tick_in_progress = True
    try:
        settings = get_settings()
        engine = get_engine()
        maker = async_sessionmaker(engine, expire_on_commit=False)
        async with try_advisory_lock(engine, SCHEDULER_TICK_LOCK) as acquired:
            if not acquired:
                log.info("scheduler tick skipped: another replica holds the tick lock")
                return summary
            async with maker() as session:
                await _run_due_checks(session, settings, summary)
                await _freshness_sweep(session, summary)
                await _roadmap_reminders(session, summary)
    finally:
        _tick_in_progress = False
    log.info(
        "scheduler tick: checks=%d failed=%d stale=%d stale_alerts=%d reminders=%d",
        summary["checks_run"],
        summary["checks_failed"],
        summary["stale_marked"],
        summary["stale_notifications"],
        summary["reminders_sent"],
    )
    return summary


async def _run_due_checks(session: AsyncSession, settings: Settings, summary: dict[str, int]) -> None:
    now = datetime.now(UTC)
    try:
        subscription_ids = list(
            (
                await session.execute(
                    select(MonitorSubscription.id)
                    .where(
                        MonitorSubscription.enabled.is_(True),
                        MonitorSubscription.next_check_at.is_not(None),
                        MonitorSubscription.next_check_at <= now,
                    )
                    .order_by(MonitorSubscription.next_check_at)
                    .limit(settings.monitor_max_checks_per_tick)
                )
            ).scalars().all()
        )
    except Exception:  # noqa: BLE001 - a broken DB must not kill the loop
        log.exception("scheduler could not load due subscriptions")
        return
    service = MonitoringService()
    for subscription_id in subscription_ids:
        try:
            await service.run_check(session, subscription_id)
            summary["checks_run"] += 1
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - one bad subscription must not stop the others
            summary["checks_failed"] += 1
            log.exception("monitor check failed for subscription %s", subscription_id)
            try:
                await session.rollback()
            except Exception:  # noqa: BLE001 - rollback itself may fail on a dead connection
                log.exception("rollback after failed monitor check also failed")


async def _freshness_sweep(session: AsyncSession, summary: dict[str, int]) -> None:
    """Mark overdue evidence STALE and alert subscribers (deduped, 7 days)."""
    now = datetime.now(UTC)
    try:
        rows = list(
            (
                await session.execute(
                    select(Evidence)
                    .where(
                        Evidence.freshness_deadline.is_not(None),
                        Evidence.freshness_deadline < now,
                        Evidence.status.notin_([EvidenceStatus.STALE, EvidenceStatus.CONFLICTING]),
                    )
                    .limit(_FRESHNESS_BATCH_LIMIT)
                )
            ).scalars().all()
        )
        if not rows:
            return
        program_claims: dict[UUID, set[str]] = {}
        for row in rows:
            row.status = EvidenceStatus.STALE
            summary["stale_marked"] += 1
            if row.subject_type == "program" and row.subject_id is not None:
                program_claims.setdefault(row.subject_id, set()).add(row.claim_type)
        await session.commit()
        if not program_claims:
            return
        pairs = list(
            (
                await session.execute(
                    select(MonitorSubscription.profile_id, MonitorSubscription.program_id).where(
                        MonitorSubscription.enabled.is_(True),
                        MonitorSubscription.program_id.is_not(None),
                        MonitorSubscription.program_id.in_(list(program_claims)),
                    )
                )
            ).all()
        )
        if not pairs:
            return
        profile_ids = {profile_id for profile_id, _program_id in pairs}
        profiles = (
            await session.execute(select(StudentProfile).where(StudentProfile.id.in_(profile_ids)))
        ).scalars().all()
        user_by_profile = {profile.id: profile.user_id for profile in profiles}
        program_ids = {program_id for _profile_id, program_id in pairs}
        programs = (
            await session.execute(select(Program).where(Program.id.in_(program_ids)))
        ).scalars().all()
        name_by_program = {program.id: program.canonical_name for program in programs}
        for profile_id, program_id in pairs:
            user_id = user_by_profile.get(profile_id)
            if user_id is None or program_id is None:
                continue
            if await has_recent_notification(
                session, user_id, "SOURCE_STALE", "program_id", str(program_id), days=7
            ):
                continue
            claim_types = sorted(program_claims.get(program_id, set()))
            name = name_by_program.get(program_id, "a monitored program")
            await create_notification(
                session,
                user_id,
                "SOURCE_STALE",
                "Source became stale",
                f"{name}: stored evidence about {', '.join(claim_types)} passed its freshness "
                "window — the value may have changed.",
                link="/monitor",
                payload={"program_id": str(program_id), "claim_types": claim_types},
                send_email=None,
            )
            summary["stale_notifications"] += 1
            summary["notifications_created"] += 1
    except asyncio.CancelledError:
        raise
    except Exception:  # noqa: BLE001 - a bad sweep must not kill the loop
        log.exception("freshness sweep failed")


async def _roadmap_reminders(session: AsyncSession, summary: dict[str, int]) -> None:
    """Remind (once per task, ever) about roadmap tasks due within 48 hours."""
    now = datetime.now(UTC)
    try:
        horizon = (now + _REMINDER_HORIZON).date()
        tasks = list(
            (
                await session.execute(
                    select(RoadmapTask)
                    .where(
                        RoadmapTask.due_date.is_not(None),
                        RoadmapTask.due_date <= horizon,
                        RoadmapTask.status.notin_([TaskStatus.DONE, TaskStatus.SKIPPED]),
                    )
                    .order_by(RoadmapTask.due_date)
                    .limit(_REMINDER_BATCH_LIMIT)
                )
            ).scalars().all()
        )
        if not tasks:
            return
        profile_ids = {task.profile_id for task in tasks}
        profiles = (
            await session.execute(select(StudentProfile).where(StudentProfile.id.in_(profile_ids)))
        ).scalars().all()
        user_by_profile = {profile.id: profile.user_id for profile in profiles}
        for task in tasks:
            user_id = user_by_profile.get(task.profile_id)
            if user_id is None or task.due_date is None:
                continue
            if await has_recent_notification(
                session, user_id, "ROADMAP_DUE", "task_id", str(task.id), days=None
            ):
                continue
            overdue = task.due_date < now.date()
            due_text = task.due_date.strftime("%d %b %Y")
            title = "Overdue roadmap task" if overdue else "Roadmap task due soon"
            body = f"{task.title} — due {due_text}"
            if overdue:
                body += " (overdue)"
            await create_notification(
                session,
                user_id,
                "ROADMAP_DUE",
                title,
                body,
                link="/dashboard",
                payload={
                    "task_id": str(task.id),
                    "due_date": task.due_date.isoformat(),
                    "task_title": task.title,
                },
                send_email="roadmap_due",
            )
            summary["reminders_sent"] += 1
            summary["notifications_created"] += 1
    except asyncio.CancelledError:
        raise
    except Exception:  # noqa: BLE001 - a bad reminder pass must not kill the loop
        log.exception("roadmap reminder pass failed")
