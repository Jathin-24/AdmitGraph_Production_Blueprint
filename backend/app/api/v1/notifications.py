"""Notification inbox endpoints.

Contract (frontend app/lib/api.ts):
    GET  /notifications                 -> {items, unread_count}   (newest first, cap 50)
    POST /notifications/{id}/read       -> {read: true}   (404 when missing/not owned)
    POST /notifications/read-all        -> {marked: n}

Importing this router also registers the event listeners defined in
``app.services.notifications`` (research.run_finished, user.registered);
main.py imports it at startup, so the bus is wired before the first request.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

# Registers the event listeners at import time (fire-and-forget on the bus).
import app.services.notifications  # noqa: F401
from app.core.errors import AppError
from app.db.models import Notification
from app.db.session import get_session
from app.services.profile import get_or_create_default_user

router = APIRouter(tags=["notifications"])

LIST_CAP = 50


@router.get("/notifications")
async def list_notifications(session: AsyncSession = Depends(get_session)) -> dict[str, Any]:
    user = await get_or_create_default_user(session)
    rows = (
        await session.execute(
            select(Notification)
            .where(Notification.user_id == user.id)
            .order_by(Notification.created_at.desc(), Notification.id.desc())
            .limit(LIST_CAP)
        )
    ).scalars().all()
    unread = (
        await session.execute(
            select(func.count(Notification.id)).where(
                Notification.user_id == user.id, Notification.read_at.is_(None)
            )
        )
    ).scalar_one()
    return {
        "items": [
            {
                "id": str(n.id),
                "type": n.type,
                "title": n.title,
                "body": n.body,
                "link": n.link,
                "read": n.read_at is not None,
                "email_status": n.email_status,
                "created_at": n.created_at.isoformat() if n.created_at else None,
            }
            for n in rows
        ],
        "unread_count": int(unread),
    }


@router.post("/notifications/{notification_id}/read")
async def mark_read(
    notification_id: uuid.UUID, session: AsyncSession = Depends(get_session)
) -> dict[str, Any]:
    user = await get_or_create_default_user(session)
    notification = await session.get(Notification, notification_id)
    if notification is None or notification.user_id != user.id:
        raise AppError(404, "NOT_FOUND", "Notification not found")
    if notification.read_at is None:
        notification.read_at = datetime.now(UTC)
        await session.commit()
    return {"read": True}


@router.post("/notifications/read-all")
async def mark_all_read(session: AsyncSession = Depends(get_session)) -> dict[str, Any]:
    user = await get_or_create_default_user(session)
    unread_ids = list(
        (
            await session.execute(
                select(Notification.id).where(
                    Notification.user_id == user.id, Notification.read_at.is_(None)
                )
            )
        ).scalars().all()
    )
    if unread_ids:
        await session.execute(
            update(Notification)
            .where(Notification.id.in_(unread_ids))
            .values(read_at=datetime.now(UTC))
        )
        await session.commit()
    return {"marked": len(unread_ids)}
