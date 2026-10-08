"""Evidence listing, conflict and member queries.

Backs the ``app.api.v1.evidence`` router; the router keeps the conflict
resolution rules (membership, already-resolved, authority preference).
"""

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Evidence, EvidenceConflict, EvidenceConflictMember, Source

# Transaction control for the evidence router (conflict resolution).
from app.db.repositories.base import commit as commit
from app.db.repositories.base import refresh as refresh


async def list_evidence(
    session: AsyncSession, program_id: uuid.UUID | None
) -> list[tuple[Evidence, Source | None]]:
    query = (
        select(Evidence, Source)
        .join(Source, Evidence.source_id == Source.id, isouter=True)
        .order_by(Evidence.retrieved_at.desc())
        .limit(50)
    )
    if program_id is not None:
        query = query.where(Evidence.subject_type == "program", Evidence.subject_id == program_id)
    rows = (await session.execute(query)).all()
    return [(evidence, source) for evidence, source in rows]


async def get_evidence(session: AsyncSession, evidence_id: uuid.UUID) -> Evidence | None:
    return await session.get(Evidence, evidence_id)


async def list_conflicts(
    session: AsyncSession, evidence_id: uuid.UUID
) -> list[EvidenceConflict]:
    result = await session.execute(
        select(EvidenceConflict)
        .join(EvidenceConflictMember, EvidenceConflictMember.conflict_id == EvidenceConflict.id)
        .where(EvidenceConflictMember.evidence_id == evidence_id)
    )
    return list(result.scalars().all())


async def get_conflict(session: AsyncSession, conflict_id: uuid.UUID) -> EvidenceConflict | None:
    return await session.get(EvidenceConflict, conflict_id)


async def list_conflict_members(session: AsyncSession, conflict_id: uuid.UUID) -> list[Evidence]:
    members = (
        await session.execute(
            select(Evidence)
            .join(
                EvidenceConflictMember,
                EvidenceConflictMember.evidence_id == Evidence.id,
            )
            .where(EvidenceConflictMember.conflict_id == conflict_id)
        )
    ).scalars().all()
    return list(members)
