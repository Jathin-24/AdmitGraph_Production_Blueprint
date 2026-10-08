"""Notification creation + in-process event listeners.

Listeners are registered at import time on the app-wide event bus
(:mod:`app.core.events`); ``app.api.v1.notifications`` imports this module so
the registration chain runs at startup (main.py imports that router).

``create_notification`` is the single helper every producer uses: it inserts
the row, commits, then attempts email delivery (never raising) and records the
outcome on ``email_status``:

* ``SENT``    — SMTP was configured and the message was accepted,
* ``FAILED``  — SMTP was configured but delivery failed,
* ``SKIPPED`` — no email was requested, or mail is disabled
  (``email_enabled=False`` / empty ``smtp_host``; the message is still written
  to the local file outbox for inspection).
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import get_settings
from app.core.errors import AppError
from app.core.events import on
from app.db.models import Notification, ResearchPlan, RunStatus, StudentProfile, User
from app.db.session import get_engine
from app.services.mail import mail_enabled
from app.services.mail import send_email as deliver_email
from app.services.mail.templates import (
    change_alert_email,
    research_complete_email,
    research_failed_email,
    roadmap_due_email,
    source_stale_email,
    welcome_email,
)

log = logging.getLogger(__name__)

EmailKind = Literal[
    "welcome",
    "research_complete",
    "research_failed",
    "change_alert",
    "source_stale",
    "roadmap_due",
]


def _json_safe(value: Any) -> Any:
    """JSONB payload values must be plain JSON (UUIDs/Decimals/dates -> str)."""
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    return value


@asynccontextmanager
async def listener_session() -> AsyncIterator[AsyncSession]:
    """Session for fire-and-forget listeners (they run outside any request)."""
    maker = async_sessionmaker(get_engine(), expire_on_commit=False)
    async with maker() as session:
        yield session


def _build_template(
    kind: EmailKind, user_name: str, link: str, headline: str
) -> tuple[str, str, str] | None:
    if kind == "welcome":
        return welcome_email(user_name, link)
    if kind == "research_complete":
        return research_complete_email(user_name, link, headline)
    if kind == "research_failed":
        return research_failed_email(user_name, link, headline)
    if kind == "change_alert":
        return change_alert_email(user_name, link, headline)
    if kind == "source_stale":
        return source_stale_email(user_name, link, headline)
    if kind == "roadmap_due":
        return roadmap_due_email(user_name, link, headline)
    log.warning("unknown email kind %r; skipping delivery", kind)
    return None


async def _deliver(user: User, notification: Notification, kind: EmailKind) -> str:
    """Send one notification email; returns the email_status to persist."""
    if not user.email:
        return "SKIPPED"
    name = user.full_name or user.email.split("@", 1)[0] or "there"
    try:
        template = _build_template(kind, name, notification.link or "/dashboard", notification.body)
    except Exception:  # noqa: BLE001 - templating must never break notification creation
        log.exception("email template failed for kind=%s", kind)
        return "FAILED"
    if template is None:
        return "SKIPPED"
    subject, html, text = template
    try:
        delivered = await deliver_email(user.email, subject, html, text)
    except Exception:  # noqa: BLE001 - send_email is already defensive; belt and braces
        log.exception("email delivery raised for kind=%s", kind)
        return "FAILED"
    if not mail_enabled(get_settings()):
        return "SKIPPED"  # written to the local outbox, not actually sent
    return "SENT" if delivered else "FAILED"


async def create_notification(
    session: AsyncSession,
    user_id: UUID,
    type: str,
    title: str,
    body: str,
    link: str | None = None,
    payload: dict[str, Any] | None = None,
    send_email: EmailKind | None = None,
) -> Notification:
    """Insert a notification for *user_id*, commit, then best-effort email."""
    user = await session.get(User, user_id)
    if user is None:
        raise AppError(404, "NOT_FOUND", "User not found")
    notification = Notification(
        user_id=user_id,
        type=type,
        title=title,
        body=body,
        link=link,
        payload=_json_safe(payload or {}),
        email_status="PENDING",
    )
    session.add(notification)
    await session.commit()

    status = "SKIPPED"
    if send_email is not None:
        status = await _deliver(user, notification, send_email)
    notification.email_status = status
    if status == "SENT":
        notification.email_sent_at = datetime.now(UTC)
    await session.commit()
    await session.refresh(notification)
    return notification


async def has_recent_notification(
    session: AsyncSession,
    user_id: UUID,
    notification_type: str,
    payload_key: str,
    payload_value: str,
    *,
    days: int | None = 7,
) -> bool:
    """True when this user already got *notification_type* for that payload value.

    ``days=None`` dedupes forever (used for roadmap reminders); the default
    window is 7 days (stale-source / conflict alerts).
    """
    stmt = (
        select(Notification.id)
        .where(
            Notification.user_id == user_id,
            Notification.type == notification_type,
            Notification.payload[payload_key].astext == str(payload_value),
        )
        .limit(1)
    )
    if days is not None:
        cutoff = datetime.now(UTC) - timedelta(days=days)
        stmt = stmt.where(Notification.created_at > cutoff)
    row = (await session.execute(stmt)).scalar_one_or_none()
    return row is not None


# --------------------------------------------------------------------- events


def _task_failed(task: asyncio.Task[Any] | None) -> bool:
    """True only when a finished run task carries an exception/cancellation."""
    if task is None:
        return False
    if task.cancelled():
        return True
    if task.done():
        return task.exception() is not None
    return False


async def on_research_run_finished(
    plan_id: UUID | str | None = None,
    task: asyncio.Task[Any] | None = None,
    **_: Any,
) -> None:
    """RESEARCH_COMPLETE / RESEARCH_FAILED notification + email for a finished run."""
    try:
        await _handle_research_run_finished(plan_id, task)
    except Exception:  # noqa: BLE001 - listeners must never break the bus
        log.exception("research.run_finished listener failed")


async def _handle_research_run_finished(
    plan_id: UUID | str | None, task: asyncio.Task[Any] | None
) -> None:
    if plan_id is None:
        return
    try:
        plan_uuid = plan_id if isinstance(plan_id, UUID) else UUID(str(plan_id))
    except (ValueError, TypeError, AttributeError):
        log.warning("research.run_finished with unparseable plan_id=%r", plan_id)
        return
    async with listener_session() as session:
        plan = await session.get(ResearchPlan, plan_uuid)
        if plan is None:
            log.info("research.run_finished for missing plan %s; skipping", plan_uuid)
            return
        profile = await session.get(StudentProfile, plan.profile_id)
        if profile is None:
            log.info("research plan %s has no profile; skipping", plan_uuid)
            return
        failed = plan.status == RunStatus.FAILED or _task_failed(task)
        succeeded = (
            plan.status in (RunStatus.SUCCEEDED, RunStatus.PARTIAL) and not _task_failed(task)
        )
        if succeeded:
            await create_notification(
                session,
                profile.user_id,
                "RESEARCH_COMPLETE",
                "Your research run is complete",
                "Your program shortlist, fit scores, risks and roadmap are ready to review.",
                link="/research",
                payload={"plan_id": str(plan.id), "status": str(plan.status)},
                send_email="research_complete",
            )
        elif failed:
            detail = (plan.error_message or "").strip()
            body = "The run could not finish"
            body += f": {detail}. " if detail else ". "
            body += "Open the research page to try again."
            await create_notification(
                session,
                profile.user_id,
                "RESEARCH_FAILED",
                "Your research run failed",
                body,
                link="/research",
                payload={"plan_id": str(plan.id), "status": str(plan.status)},
                send_email="research_failed",
            )
        else:
            log.debug(
                "research.run_finished with inconclusive status %s; no notification",
                plan.status,
            )


async def on_user_registered(
    user_id: Any = None, email: Any = "", full_name: Any = "", **_: Any
) -> None:
    """WELCOME notification + welcome email for a newly registered account."""
    try:
        await _handle_user_registered(user_id, email, full_name)
    except Exception:  # noqa: BLE001 - listeners must never break the bus
        log.exception("user.registered listener failed")


async def _handle_user_registered(user_id: Any, email: Any, full_name: Any) -> None:
    try:
        user_uuid = user_id if isinstance(user_id, UUID) else UUID(str(user_id))
    except (ValueError, TypeError, AttributeError):
        log.warning("user.registered with unparseable user_id=%r", user_id)
        return
    name = str(full_name or "").strip()
    async with listener_session() as session:
        user = await session.get(User, user_uuid)
        if user is None:
            log.info("user.registered for missing user %s; skipping", user_uuid)
            return
        display = name or str(email or "").split("@", 1)[0] or "there"
        await create_notification(
            session,
            user_uuid,
            "WELCOME",
            "Welcome to AdmitGraph",
            f"Hi {display} — finish onboarding and your first evidence-backed plan is a few "
            "minutes away.",
            link="/onboarding",
            payload={"email": str(email or user.email or "")},
            send_email="welcome",
        )


on("research.run_finished", on_research_run_finished)
on("user.registered", on_user_registered)
