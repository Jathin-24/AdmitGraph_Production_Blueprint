"""Monitor subscription and snapshot queries.

Backs the ``app.api.v1.monitor`` router; owner scoping stays in the router
(and in MonitoringService.run_check).
"""

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import MonitorSnapshot, MonitorSubscription, Program


async def add_subscription(session: AsyncSession, sub: MonitorSubscription) -> None:
    session.add(sub)
    await session.commit()


async def list_subscriptions(
    session: AsyncSession, profile_id: uuid.UUID
) -> list[tuple[MonitorSubscription, Program | None]]:
    rows = (
        await session.execute(
            select(MonitorSubscription, Program)
            .outerjoin(Program, MonitorSubscription.program_id == Program.id)
            .where(MonitorSubscription.profile_id == profile_id)
        )
    ).all()
    return [(subscription, program) for subscription, program in rows]


async def get_subscription(
    session: AsyncSession, subscription_id: uuid.UUID
) -> MonitorSubscription | None:
    return await session.get(MonitorSubscription, subscription_id)


async def list_snapshots(
    session: AsyncSession, subscription_id: uuid.UUID
) -> list[MonitorSnapshot]:
    result = await session.execute(
        select(MonitorSnapshot)
        .where(MonitorSnapshot.subscription_id == subscription_id)
        .order_by(MonitorSnapshot.checked_at.desc())
    )
    return list(result.scalars().all())
