"""Program browsing, requirements, evidence and save/unsave endpoints."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError
from app.db.models import Evidence, Program, Requirement, SavedProgram, Source
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
    query = select(Program)
    if saved_only:
        query = query.join(SavedProgram, SavedProgram.program_id == Program.id).where(
            SavedProgram.profile_id == profile.id
        )
    total = (await session.execute(select(func.count()).select_from(query.subquery()))).scalar_one()
    rows = (
        await session.execute(
            query.order_by(Program.canonical_name).offset((page - 1) * page_size).limit(page_size)
        )
    ).scalars().all()
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
    program = await session.get(Program, program_id)
    if program is None:
        raise AppError(404, "NOT_FOUND", "Program not found")
    return program


@router.get("/programs/{program_id}")
async def get_program(
    program_id: uuid.UUID, session: AsyncSession = Depends(get_session)
) -> dict[str, Any]:
    from sqlalchemy.orm import selectinload

    program = (
        await session.execute(
            select(Program).where(Program.id == program_id).options(selectinload(Program.institution))
        )
    ).scalar_one_or_none()
    if program is None:
        raise AppError(404, "NOT_FOUND", "Program not found")
    req_count = (
        await session.execute(
            select(func.count()).select_from(Requirement).where(Requirement.program_id == program_id)
        )
    ).scalar_one()
    ev_count = (
        await session.execute(
            select(func.count())
            .select_from(Evidence)
            .where(Evidence.subject_type == "program", Evidence.subject_id == program_id)
        )
    ).scalar_one()
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
    rows = (
        await session.execute(
            select(Requirement)
            .where(Requirement.program_id == program_id)
            .order_by(Requirement.normalized_key)
        )
    ).scalars().all()
    # Best-effort evidence join: evidence for this program sharing the
    # requirement's normalized key (MASTER_SPEC §7: requirements carry refs).
    evidence_rows = (
        await session.execute(
            select(Evidence.normalized_claim, Evidence.id).where(
                Evidence.subject_type == "program",
                Evidence.subject_id == program_id,
                Evidence.normalized_claim.is_not(None),
            )
        )
    ).all()
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
    rows = (
        await session.execute(
            select(Evidence, Source)
            .join(Source, Evidence.source_id == Source.id, isouter=True)
            .where(Evidence.subject_type == "program", Evidence.subject_id == program_id)
            .order_by(Evidence.retrieved_at.desc())
            .limit(100)
        )
    ).all()
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
    existing = (
        await session.execute(
            select(SavedProgram).where(
                SavedProgram.profile_id == profile.id, SavedProgram.program_id == program_id
            )
        )
    ).scalar_one_or_none()
    if existing is None:
        existing = SavedProgram(profile_id=profile.id, program_id=program_id)
        session.add(existing)
        await session.commit()
    return {"saved": True, "program_id": str(program_id)}


@router.delete("/programs/{program_id}/save")
async def unsave_program(
    program_id: uuid.UUID, session: AsyncSession = Depends(get_session)
) -> dict[str, Any]:
    profile = await get_or_create_profile(session)
    row = (
        await session.execute(
            select(SavedProgram).where(
                SavedProgram.profile_id == profile.id, SavedProgram.program_id == program_id
            )
        )
    ).scalar_one_or_none()
    if row is not None:
        await session.delete(row)
        await session.commit()
    return {"saved": False, "program_id": str(program_id)}
