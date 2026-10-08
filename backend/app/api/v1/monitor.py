"""Monitor subscription endpoints (api/API_CONTRACT.md §Monitoring)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError
from app.db.models import MonitorSnapshot, MonitorSubscription, Program
from app.db.session import get_session
from app.services.monitoring.materiality import FIELD_KEYS, FREQUENCY_DELTAS, next_check_time
from app.services.monitoring.service import (
    MonitoringService,
    explanation_for_snapshot,
    explanations_for_snapshots,
)
from app.services.profile import get_or_create_profile

router = APIRouter(tags=["monitor"])


@router.post("/monitor/subscriptions")
async def create_subscription(
    payload: dict[str, Any], session: AsyncSession = Depends(get_session)
) -> dict[str, str]:
    field_key = payload.get("field_key")
    if field_key not in FIELD_KEYS:
        raise AppError(
            400,
            "VALIDATION_ERROR",
            f"field_key must be one of: {', '.join(FIELD_KEYS)}",
        )
    frequency = str(payload.get("frequency") or "WEEKLY").strip().upper()
    if frequency not in FREQUENCY_DELTAS:
        raise AppError(
            400,
            "VALIDATION_ERROR",
            f"frequency must be one of: {', '.join(sorted(FREQUENCY_DELTAS))}",
        )
    raw_program = payload.get("program_id")
    program_id: uuid.UUID | None = None
    if raw_program:
        try:
            program_id = uuid.UUID(str(raw_program))
        except (ValueError, AttributeError, TypeError) as exc:
            raise AppError(400, "VALIDATION_ERROR", "program_id must be a UUID") from exc
    profile = await get_or_create_profile(session)
    sub = MonitorSubscription(
        profile_id=profile.id,
        program_id=program_id,
        field_key=str(field_key),
        frequency=frequency,
        next_check_at=next_check_time(datetime.now(UTC), frequency),
    )
    session.add(sub)
    await session.commit()
    return {"id": str(sub.id)}


@router.get("/monitor/subscriptions")
async def list_subscriptions(session: AsyncSession = Depends(get_session)) -> dict[str, Any]:
    rows = (
        await session.execute(
            select(MonitorSubscription, Program)
            .outerjoin(Program, MonitorSubscription.program_id == Program.id)
        )
    ).all()
    return {
        "items": [
            {
                "id": str(s.id),
                "field_key": s.field_key,
                "frequency": s.frequency,
                "enabled": s.enabled,
                "program_id": str(s.program_id) if s.program_id else None,
                "program_name": p.canonical_name if p else None,
                "next_check_at": s.next_check_at.isoformat() if s.next_check_at else None,
                "last_checked_at": s.last_checked_at.isoformat() if s.last_checked_at else None,
            }
            for s, p in rows
        ]
    }


@router.post("/monitor/subscriptions/{subscription_id}/check")
async def run_check(
    subscription_id: uuid.UUID, session: AsyncSession = Depends(get_session)
) -> dict[str, Any]:
    snapshot = await MonitoringService().run_check(session, subscription_id)
    explanation = await explanation_for_snapshot(session, snapshot, include_immaterial=True)
    return {
        "id": str(snapshot.id),
        "change_type": snapshot.change_type,
        "material_change": snapshot.material_change,
        "old_value": snapshot.old_value,
        "new_value": snapshot.new_value,
        "explanation": explanation,
    }


@router.get("/monitor/subscriptions/{subscription_id}/changes")
async def changes(
    subscription_id: uuid.UUID, session: AsyncSession = Depends(get_session)
) -> dict[str, Any]:
    result = await session.execute(
        select(MonitorSnapshot)
        .where(MonitorSnapshot.subscription_id == subscription_id)
        .order_by(MonitorSnapshot.checked_at.desc())
    )
    snapshots = list(result.scalars().all())
    explanations = await explanations_for_snapshots(session, snapshots)
    return {
        "items": [
            {
                "id": str(s.id),
                "change_type": s.change_type,
                "material_change": s.material_change,
                "checked_at": s.checked_at.isoformat(),
                "old_value": s.old_value,
                "new_value": s.new_value,
                "explanation": explanations.get(s.id),
            }
            for s in snapshots
        ]
    }
