"""On-demand monitoring: compare current evidence to previous snapshot, flag material changes."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Evidence, MonitorSnapshot, MonitorSubscription


def is_material_change(old: Any, new: Any) -> bool:
    return old != new and old is not None and new is not None


class MonitoringService:
    async def run_check(self, session: AsyncSession, subscription_id: UUID) -> MonitorSnapshot:
        sub = await session.get(MonitorSubscription, subscription_id)
        if sub is None:
            raise ValueError("Subscription not found")

        previous = await session.execute(
            select(MonitorSnapshot)
            .where(
                MonitorSnapshot.subscription_id == subscription_id,
                MonitorSnapshot.field_key == sub.field_key,
            )
            .order_by(MonitorSnapshot.checked_at.desc())
            .limit(1)
        )
        prev = previous.scalar_one_or_none()
        old_value = prev.new_value if prev else None

        evidence_query = (
            select(Evidence)
            .where(Evidence.subject_type == "program")
            .where(Evidence.claim_type == sub.field_key)
            .order_by(Evidence.retrieved_at.desc())
            .limit(1)
        )
        if sub.program_id is not None:
            evidence_query = evidence_query.where(Evidence.subject_id == sub.program_id)
        result = await session.execute(evidence_query)
        latest = result.scalar_one_or_none()
        new_value = latest.extracted_value if latest else None

        snapshot = MonitorSnapshot(
            subscription_id=subscription_id,
            field_key=sub.field_key,
            old_value=old_value,
            new_value=new_value,
            change_type="UPDATED" if is_material_change(old_value, new_value) else "UNCHANGED",
            evidence_ids=[str(latest.id)] if latest else [],
            material_change=is_material_change(old_value, new_value),
            checked_at=datetime.now(UTC),
        )
        session.add(snapshot)
        sub.last_checked_at = datetime.now(UTC)
        await session.commit()
        await session.refresh(snapshot)
        return snapshot
