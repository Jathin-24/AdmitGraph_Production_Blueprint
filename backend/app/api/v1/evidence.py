import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError
from app.db.models import Evidence
from app.db.repositories import evidence as evidence_repo
from app.db.session import get_session

router = APIRouter(tags=["evidence"])


class ResolveConflictIn(BaseModel):
    """Optional body: resolve without a body to accept the default preference."""

    preferred_evidence_id: uuid.UUID | None = None
    reason: str | None = None


@router.get("/evidence")
async def list_evidence(
    program_id: uuid.UUID | None = None, session: AsyncSession = Depends(get_session)
) -> dict[str, Any]:
    rows = await evidence_repo.list_evidence(session, program_id)
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
    row = await evidence_repo.get_evidence(session, evidence_id)
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


@router.post("/evidence/{evidence_id}/recheck")
async def recheck_evidence(
    evidence_id: uuid.UUID, session: AsyncSession = Depends(get_session)
) -> dict[str, Any]:
    """Bounded fresh re-check of one claim (1 search + extraction).

    409 PROVIDER_UNAVAILABLE when SERPAPI_API_KEY is not configured.
    """
    from app.services.evidence.recheck import recheck_evidence as run_recheck

    return await run_recheck(session, evidence_id)


@router.get("/evidence/{evidence_id}/conflicts")
async def get_conflicts(
    evidence_id: uuid.UUID, session: AsyncSession = Depends(get_session)
) -> dict[str, Any]:
    conflicts = await evidence_repo.list_conflicts(session, evidence_id)
    return {
        "evidence_id": str(evidence_id),
        "conflicts": [
            {
                "id": str(c.id),
                "conflict_key": c.conflict_key,
                "description": c.description,
                "resolution_status": c.resolution_status,
                "preferred_evidence_id": str(c.preferred_evidence_id) if c.preferred_evidence_id else None,
                "resolution_reason": c.resolution_reason,
                "resolved_at": c.resolved_at.isoformat() if c.resolved_at else None,
            }
            for c in conflicts
        ],
    }


@router.post("/evidence/conflicts/{conflict_id}/resolve")
async def resolve_conflict(
    conflict_id: uuid.UUID,
    payload: ResolveConflictIn | None = None,
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Resolve a conflict group on the user's say-so.

    `preferred_evidence_id` defaults to the authority-preferred member recorded
    at detection time; only members of THIS conflict may be chosen.

    Errors: 404 NOT_FOUND for an unknown conflict or evidence id, 409
    ALREADY_RESOLVED when the conflict is already resolved, 409
    EVIDENCE_NOT_IN_CONFLICT when the chosen evidence is not a member.
    """
    from app.services.evidence.conflicts import authority_preferred_evidence

    body = payload or ResolveConflictIn()
    conflict = await evidence_repo.get_conflict(session, conflict_id)
    if conflict is None:
        raise AppError(404, "NOT_FOUND", "Conflict not found")
    if conflict.resolution_status == "RESOLVED":
        raise AppError(409, "ALREADY_RESOLVED", "Conflict is already resolved")

    members = await evidence_repo.list_conflict_members(session, conflict.id)
    member_ids = {m.id for m in members}
    if not member_ids:
        raise AppError(409, "EMPTY_CONFLICT", "Conflict has no evidence members")

    preferred: Evidence | None
    if body.preferred_evidence_id is not None:
        chosen = await evidence_repo.get_evidence(session, body.preferred_evidence_id)
        if chosen is None:
            raise AppError(404, "NOT_FOUND", "Evidence not found")
        if chosen.id not in member_ids:
            raise AppError(
                409, "EVIDENCE_NOT_IN_CONFLICT", "Evidence is not a member of this conflict"
            )
        preferred = chosen
    else:
        preferred = await authority_preferred_evidence(session, list(members))

    now = datetime.now(UTC)
    conflict.preferred_evidence_id = preferred.id if preferred is not None else None
    conflict.resolution_status = "RESOLVED"
    conflict.resolved_at = now
    reason = (body.reason or "").strip()
    conflict.resolution_reason = reason or None
    await evidence_repo.commit(session)
    await evidence_repo.refresh(session, conflict)
    return {
        "id": str(conflict.id),
        "conflict_key": conflict.conflict_key,
        "resolution_status": conflict.resolution_status,
        "preferred_evidence_id": (
            str(conflict.preferred_evidence_id) if conflict.preferred_evidence_id else None
        ),
        "resolution_reason": conflict.resolution_reason,
        "resolved_at": conflict.resolved_at.isoformat() if conflict.resolved_at else None,
    }
