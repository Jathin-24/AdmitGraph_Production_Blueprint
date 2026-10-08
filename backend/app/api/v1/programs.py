"""Program browsing, requirements, evidence and save/unsave endpoints."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError
from app.db.models import Evidence, Program, SavedProgram, Source
from app.db.repositories import programs as programs_repo
from app.db.session import get_session
from app.services.profile import get_or_create_profile

router = APIRouter(tags=["programs"])


@router.get("/programs")
async def list_programs(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    saved_only: bool = False,
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    profile = await get_or_create_profile(session)
    total, rows = await programs_repo.list_programs(
        session, profile.id, saved_only, (page - 1) * page_size, page_size
    )
    has_more = page * page_size < total
    return {
        "items": [
            {
                "id": str(p.id),
                "name": p.canonical_name,
                "country_code": p.country_code,
                "degree_type": p.degree_type,
                "field_of_study": p.field_of_study,
                "official_url": p.official_url,
            }
            for p in rows
        ],
        "page": page,
        "page_size": page_size,
        "total": total,
        "next_cursor": str(page + 1) if has_more else None,
    }


async def _get_program(session: AsyncSession, program_id: uuid.UUID) -> Program:
    program = await programs_repo.get_program_by_id(session, program_id)
    if program is None:
        raise AppError(404, "NOT_FOUND", "Program not found")
    return program


@router.get("/programs/{program_id}")
async def get_program(
    program_id: uuid.UUID, session: AsyncSession = Depends(get_session)
) -> dict[str, Any]:
    program = await programs_repo.get_program_with_institution(session, program_id)
    if program is None:
        raise AppError(404, "NOT_FOUND", "Program not found")
    req_count = await programs_repo.count_requirements(session, program_id)
    ev_count = await programs_repo.count_program_evidence(session, program_id)
    return {
        "id": str(program.id),
        "name": program.canonical_name,
        "institution": program.institution.canonical_name if program.institution else None,
        "institution_domain": program.institution.domain if program.institution else None,
        "country_code": program.country_code,
        "city": program.city,
        "degree_type": program.degree_type,
        "field_of_study": program.field_of_study,
        "specialization": program.specialization,
        "language": program.language,
        "official_url": program.official_url,
        "duration_months": program.duration_months,
        "tuition_amount": str(program.tuition_amount) if program.tuition_amount else None,
        "tuition_currency": program.tuition_currency,
        "active": program.active,
        "last_verified_at": program.last_verified_at.isoformat() if program.last_verified_at else None,
        "requirement_count": req_count,
        "evidence_count": ev_count,
    }


@router.get("/programs/{program_id}/requirements")
async def get_program_requirements(
    program_id: uuid.UUID, session: AsyncSession = Depends(get_session)
) -> dict[str, Any]:
    await _get_program(session, program_id)
    rows = await programs_repo.list_requirements(session, program_id)
    # Best-effort evidence join: evidence for this program sharing the
    # requirement's normalized key (MASTER_SPEC §7: requirements carry refs).
    evidence_rows = await programs_repo.list_program_evidence_claims(session, program_id)
    evidence_by_key: dict[str, list[str]] = {}
    for key, evidence_id in evidence_rows:
        evidence_by_key.setdefault(str(key), []).append(str(evidence_id))
    return {
        "items": [
            {
                "id": str(r.id),
                "requirement_type": r.requirement_type,
                "title": r.title,
                "normalized_key": r.normalized_key,
                "operator": r.operator,
                "value": r.value,
                "mandatory": r.mandatory,
                "status": r.status.value,
                "last_verified_at": r.last_verified_at.isoformat() if r.last_verified_at else None,
                "evidence_ids": evidence_by_key.get(r.normalized_key, []),
            }
            for r in rows
        ]
    }


@router.get("/programs/{program_id}/evidence")
async def get_program_evidence(
    program_id: uuid.UUID, session: AsyncSession = Depends(get_session)
) -> dict[str, Any]:
    await _get_program(session, program_id)
    rows = await programs_repo.list_program_evidence(session, program_id)
    return {"items": _evidence_rows(rows)}


def _evidence_rows(rows: Sequence[tuple[Evidence, Source | None]]) -> list[dict[str, Any]]:
    return [
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
            "source_id": str(e.source_id),
            "source_url": s.url if s else None,
            "source_domain": s.domain if s else None,
            "source_authority": s.source_authority.value if s else None,
            "search_result_id": str(e.search_result_id) if e.search_result_id else None,
        }
        for e, s in rows
    ]


@router.post("/programs/{program_id}/save")
async def save_program(
    program_id: uuid.UUID, session: AsyncSession = Depends(get_session)
) -> dict[str, Any]:
    await _get_program(session, program_id)
    profile = await get_or_create_profile(session)
    existing = await programs_repo.get_saved_program(session, profile.id, program_id)
    if existing is None:
        await programs_repo.save_program(
            session, SavedProgram(profile_id=profile.id, program_id=program_id)
        )
    return {"saved": True, "program_id": str(program_id)}


@router.delete("/programs/{program_id}/save")
async def unsave_program(
    program_id: uuid.UUID, session: AsyncSession = Depends(get_session)
) -> dict[str, Any]:
    profile = await get_or_create_profile(session)
    row = await programs_repo.get_saved_program(session, profile.id, program_id)
    if row is not None:
        await programs_repo.delete_saved_program(session, row)
    return {"saved": False, "program_id": str(program_id)}
