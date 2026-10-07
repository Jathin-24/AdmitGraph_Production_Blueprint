import uuid
from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError
from app.db.models import Evidence, EvidenceConflict, EvidenceConflictMember, Source
from app.db.session import get_session

router = APIRouter(tags=["evidence"])


@router.get("/evidence")
async def list_evidence(
    program_id: uuid.UUID | None = None, session: AsyncSession = Depends(get_session)
) -> dict[str, Any]:
    query = (
        select(Evidence, Source)
        .join(Source, Evidence.source_id == Source.id, isouter=True)
        .order_by(Evidence.retrieved_at.desc())
        .limit(50)
    )
    if program_id is not None:
        query = query.where(Evidence.subject_type == "program", Evidence.subject_id == program_id)
    rows = (await session.execute(query)).all()
    return {
        "items": [
            {
                "id": str(e.id),
                "claim_type": e.claim_type,
                "claim": e.claim,
                "normalized_claim": e.normalized_claim,
                "confidence": e.confidence.value,
                "status": e.status.value,
                "retrieved_at": e.retrieved_at.isoformat() if e.retrieved_at else None,
                "freshness_deadline": (
                    e.freshness_deadline.isoformat() if e.freshness_deadline else None
                ),
                "subject_id": str(e.subject_id) if e.subject_id else None,
                "source_url": s.url if s else None,
                "source_domain": s.domain if s else None,
                "source_authority": s.source_authority.value if s else None,
            }
            for e, s in rows
        ]
    }


@router.get("/evidence/{evidence_id}")
async def get_evidence(
    evidence_id: uuid.UUID, session: AsyncSession = Depends(get_session)
) -> dict[str, Any]:
    row = await session.get(Evidence, evidence_id)
    if row is None:
        raise AppError(404, "NOT_FOUND", "Evidence not found")
    return {
        "id": str(row.id),
        "claim_type": row.claim_type,
        "subject_type": row.subject_type,
        "subject_id": str(row.subject_id) if row.subject_id else None,
        "claim": row.claim,
        "normalized_claim": row.normalized_claim,
        "extracted_value": row.extracted_value,
        "confidence": row.confidence.value,
        "status": row.status.value,
        "retrieved_at": row.retrieved_at.isoformat() if row.retrieved_at else None,
        "freshness_deadline": row.freshness_deadline.isoformat() if row.freshness_deadline else None,
        "extraction_version": row.extraction_version,
        "source_id": str(row.source_id),
        "search_result_id": str(row.search_result_id) if row.search_result_id else None,
    }


@router.get("/evidence/{evidence_id}/conflicts")
async def get_conflicts(
    evidence_id: uuid.UUID, session: AsyncSession = Depends(get_session)
) -> dict[str, Any]:
    result = await session.execute(
        select(EvidenceConflict)
        .join(EvidenceConflictMember, EvidenceConflictMember.conflict_id == EvidenceConflict.id)
        .where(EvidenceConflictMember.evidence_id == evidence_id)
    )
    conflicts = result.scalars().all()
    return {
        "evidence_id": str(evidence_id),
        "conflicts": [
            {
                "id": str(c.id),
                "conflict_key": c.conflict_key,
                "description": c.description,
                "resolution_status": c.resolution_status,
                "resolved_at": c.resolved_at.isoformat() if c.resolved_at else None,
            }
            for c in conflicts
        ],
    }
