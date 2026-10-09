"""Monitor subscription, snapshot and check queries.

Backs the ``app.api.v1.monitor`` router and ``app.services.monitoring``;
owner scoping stays in the router (and in MonitoringService.run_check). Every
SQL statement the on-demand check runs — baselines, cached evidence, source
domains, snapshots — lives here.
"""

import uuid
from datetime import datetime

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    Evidence,
    EvidenceStatus,
    Institution,
    MonitorSnapshot,
    MonitorSubscription,
    Program,
    Source,
)


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


async def latest_snapshot(
    session: AsyncSession, subscription_id: uuid.UUID, field_key: str
) -> MonitorSnapshot | None:
    """Most recent snapshot for one watched field (the check's baseline)."""
    snapshot: MonitorSnapshot | None = (
        await session.execute(
            select(MonitorSnapshot)
            .where(
                MonitorSnapshot.subscription_id == subscription_id,
                MonitorSnapshot.field_key == field_key,
            )
            .order_by(MonitorSnapshot.checked_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    return snapshot


async def evidence_retrieved_map(
    session: AsyncSession, ids: list[uuid.UUID]
) -> dict[uuid.UUID, datetime]:
    """retrieved_at per evidence id (for the live/cached explanation)."""
    if not ids:
        return {}
    rows = await session.execute(select(Evidence.id, Evidence.retrieved_at).where(Evidence.id.in_(ids)))
    return {row_id: retrieved for row_id, retrieved in rows.all()}


async def latest_evidence_source_domain(
    session: AsyncSession, *, program_id: uuid.UUID | None, claim_types: tuple[str, ...]
) -> str | None:
    """Official domain hint from the evidence already stored for this subject."""
    stmt = (
        select(Source.domain)
        .join(Evidence, Evidence.source_id == Source.id)
        .where(Evidence.subject_type == "program")
        .order_by(Evidence.retrieved_at.desc())
        .limit(1)
    )
    if program_id is not None:
        stmt = stmt.where(Evidence.subject_id == program_id)
    else:
        stmt = stmt.where(Evidence.claim_type.in_(claim_types))
    domain = (await session.execute(stmt)).scalar_one_or_none()
    return domain


async def institution_domain(session: AsyncSession, institution_id: uuid.UUID) -> str | None:
    domain = (
        await session.execute(select(Institution.domain).where(Institution.id == institution_id))
    ).scalar_one_or_none()
    return domain


async def source_by_canonical_url(session: AsyncSession, canonical_url: str) -> Source | None:
    source: Source | None = (
        await session.execute(select(Source).where(Source.canonical_url == canonical_url))
    ).scalar_one_or_none()
    return source


async def latest_cached_evidence(
    session: AsyncSession,
    *,
    program_id: uuid.UUID | None,
    claim_types: tuple[str, ...],
    scholarship_like: bool,
) -> Evidence | None:
    """Latest stored evidence for a watched field (honest cached fallback)."""
    conditions = [Evidence.claim_type.in_(claim_types)]
    if scholarship_like:
        conditions.append(Evidence.normalized_claim.ilike("%scholarship%"))
    stmt = select(Evidence).where(Evidence.subject_type == "program", or_(*conditions))
    if program_id is not None:
        stmt = stmt.where(Evidence.subject_id == program_id)
    latest: Evidence | None = (
        await session.execute(stmt.order_by(Evidence.retrieved_at.desc()).limit(1))
    ).scalar_one_or_none()
    return latest


async def conflicting_evidence_id(
    session: AsyncSession, program_id: uuid.UUID
) -> uuid.UUID | None:
    """Any stored CONFLICTING evidence row for the program, if one exists."""
    row = (
        await session.execute(
            select(Evidence.id)
            .where(
                Evidence.subject_type == "program",
                Evidence.subject_id == program_id,
                Evidence.status == EvidenceStatus.CONFLICTING,
            )
            .limit(1)
        )
    ).scalar_one_or_none()
    return row
