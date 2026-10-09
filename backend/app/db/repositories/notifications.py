"""Notification inbox queries.

Backs the ``app.api.v1.notifications`` router; the router keeps the response
shaping (item dicts, unread counts, the read-at stamping decision). The
producer-side dedupe query used by ``app.services.notifications`` lives here
too, so no SQL statement appears under the service.
"""

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Notification

# Transaction control for the notifications router (mark-read endpoints).
from app.db.repositories.base import commit as commit


async def list_notifications(
    session: AsyncSession, user_id: uuid.UUID, limit: int
) -> list[Notification]:
    rows = (
        await session.execute(
            select(Notification)
            .where(Notification.user_id == user_id)
            .order_by(Notification.created_at.desc(), Notification.id.desc())
            .limit(limit)
        )
    ).scalars().all()
    return list(rows)


async def count_unread(session: AsyncSession, user_id: uuid.UUID) -> int:
    unread: int = (
        await session.execute(
            select(func.count(Notification.id)).where(
                Notification.user_id == user_id, Notification.read_at.is_(None)
            )
        )
    ).scalar_one()
    return unread


async def get_notification(
    session: AsyncSession, notification_id: uuid.UUID
) -> Notification | None:
    return await session.get(Notification, notification_id)


async def list_unread_ids(session: AsyncSession, user_id: uuid.UUID) -> list[uuid.UUID]:
    unread_ids = list(
        (
            await session.execute(
                select(Notification.id).where(
                    Notification.user_id == user_id, Notification.read_at.is_(None)
                )
            )
        ).scalars().all()
    )
    return unread_ids


async def mark_notifications_read(
    session: AsyncSession, notification_ids: list[uuid.UUID]
) -> None:
    await session.execute(
        update(Notification)
        .where(Notification.id.in_(notification_ids))
        .values(read_at=datetime.now(UTC))
    )
    await session.commit()


async def has_recent_notification(
    session: AsyncSession,
    user_id: uuid.UUID,
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
