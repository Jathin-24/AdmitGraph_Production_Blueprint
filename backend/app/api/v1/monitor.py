"""Monitor subscription endpoints (api/API_CONTRACT.md §Monitoring)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError
from app.db.models import MonitorSubscription
from app.db.repositories import monitor as monitor_repo
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
    await monitor_repo.add_subscription(session, sub)
    return {"id": str(sub.id)}


@router.get("/monitor/subscriptions")
async def list_subscriptions(session: AsyncSession = Depends(get_session)) -> dict[str, Any]:
    profile = await get_or_create_profile(session)
    rows = await monitor_repo.list_subscriptions(session, profile.id)
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
    # Owner scoping: another user's subscription is indistinguishable from a
    # missing one (404), enforced inside MonitoringService.run_check.
    profile = await get_or_create_profile(session)
    snapshot = await MonitoringService().run_check(
        session, subscription_id, owner_profile_id=profile.id
    )
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
    # Snapshot history is scoped through its subscription: a foreign or
    # unknown subscription id is a 404, never an empty 200 (cross-user probe).
    profile = await get_or_create_profile(session)
    subscription = await monitor_repo.get_subscription(session, subscription_id)
    if subscription is None or subscription.profile_id != profile.id:
        raise AppError(404, "NOT_FOUND", "Monitor subscription not found")
    snapshots = await monitor_repo.list_snapshots(session, subscription_id)
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
