import uuid
from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import MonitorSnapshot, MonitorSubscription
from app.db.session import get_session
from app.services.monitoring.service import MonitoringService
from app.services.profile import get_or_create_profile

router = APIRouter(tags=["monitor"])


@router.post("/monitor/subscriptions")
async def create_subscription(
    payload: dict[str, Any], session: AsyncSession = Depends(get_session)
) -> dict[str, str]:
    profile = await get_or_create_profile(session)
    sub = MonitorSubscription(
        profile_id=profile.id,
        program_id=uuid.UUID(payload["program_id"]) if payload.get("program_id") else None,
        field_key=payload["field_key"],
        frequency=payload.get("frequency", "WEEKLY"),
    )
    session.add(sub)
    await session.commit()
    return {"id": str(sub.id)}


@router.get("/monitor/subscriptions")
async def list_subscriptions(session: AsyncSession = Depends(get_session)) -> dict[str, Any]:
    result = await session.execute(select(MonitorSubscription))
    return {
        "items": [
            {"id": str(s.id), "field_key": s.field_key, "frequency": s.frequency, "enabled": s.enabled}
            for s in result.scalars().all()
        ]
    }


@router.post("/monitor/subscriptions/{subscription_id}/check")
async def run_check(
    subscription_id: uuid.UUID, session: AsyncSession = Depends(get_session)
) -> dict[str, Any]:
    snapshot = await MonitoringService().run_check(session, subscription_id)
    return {
        "id": str(snapshot.id),
        "change_type": snapshot.change_type,
        "material_change": snapshot.material_change,
        "old_value": snapshot.old_value,
        "new_value": snapshot.new_value,
    }


@router.get("/monitor/subscriptions/{subscription_id}/changes")
async def changes(subscription_id: uuid.UUID, session: AsyncSession = Depends(get_session)) -> dict[str, Any]:
    result = await session.execute(
        select(MonitorSnapshot)
        .where(MonitorSnapshot.subscription_id == subscription_id)
        .order_by(MonitorSnapshot.checked_at.desc())
    )
    return {
        "items": [
            {
                "id": str(s.id),
                "change_type": s.change_type,
                "material_change": s.material_change,
                "checked_at": s.checked_at.isoformat(),
            }
            for s in result.scalars().all()
        ]
    }
