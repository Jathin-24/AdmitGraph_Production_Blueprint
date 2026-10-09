"""Program browsing, requirement, evidence and save/unsave queries.

Backs the ``app.api.v1.programs`` router; the router keeps the response
shaping (item dicts, pagination cursors, the evidence join rows helper).
"""

import uuid
from typing import Any, Literal

from sqlalchemy import func, or_, select
from sqlalchemy.dialects.postgresql import distinct_on
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from sqlalchemy.sql.elements import ColumnElement

from app.db.models import (
    Evidence,
    FitAssessment,
    Institution,
    Intake,
    Program,
    Requirement,
    SavedProgram,
    Source,
)

ProgramSort = Literal["fit_score", "name", "deadline"]


def escape_like(text: str) -> str:
    """Escape LIKE/ILIKE wildcards so ``q`` is a literal substring search."""
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


async def list_programs(
    session: AsyncSession,
    profile_id: uuid.UUID,
    saved_only: bool,
    offset: int,
    limit: int,
    *,
    q: str = "",
    country: str | None = None,
    degree_level: str | None = None,
    sort: ProgramSort = "fit_score",
) -> tuple[int, list[Program]]:
    """Filtered, sorted, paginated catalog listing (P2-13).

    * ``q`` — case-insensitive substring over program name, institution name
      and city (wildcards escaped, so ``%`` is a literal character);
    * ``country`` / ``degree_level`` — case-insensitive exact matches on
      ``Program.country_code`` / ``Program.degree_type``;
    * ``sort`` — ``fit_score`` (latest fit assessment for this profile,
      descending, programs without a score last), ``name`` (ascending) or
      ``deadline`` (earliest upcoming application deadline first, undated
      last). Ties break on canonical_name then id for stable paging.
    """
    query = select(Program)
    if saved_only:
        query = query.join(SavedProgram, SavedProgram.program_id == Program.id).where(
            SavedProgram.profile_id == profile_id
        )
    # Institution join is only needed for the free-text search.
    needs_institution = bool(q and q.strip())
    if needs_institution:
        query = query.join(Program.institution, isouter=True)

    conditions: list[ColumnElement[bool]] = []
    pattern = f"%{escape_like(q.strip())}%"
    if needs_institution:
        conditions.append(
            or_(
                Program.canonical_name.ilike(pattern, escape="\\"),
                Program.city.ilike(pattern, escape="\\"),
                Institution.canonical_name.ilike(pattern, escape="\\"),
            )
        )
    if country and country.strip():
        conditions.append(func.upper(Program.country_code) == country.strip().upper())
    if degree_level and degree_level.strip():
        conditions.append(func.upper(func.coalesce(Program.degree_type, "")) == degree_level.strip().upper())
    if conditions:
        query = query.where(*conditions)

    total: int = (
        await session.execute(select(func.count()).select_from(query.subquery()))
    ).scalar_one()

    # sort=fit_score → LEFT JOIN the caller's latest assessment per program
    # (DISTINCT ON keeps exactly one row per program, so the count above and
    # row cardinality are unaffected by multiple assessments).
    order_by: list[ColumnElement[Any]] = [Program.canonical_name.asc(), Program.id.asc()]
    if sort == "fit_score":
        latest_fit = (
            select(
                FitAssessment.program_id.label("fit_program_id"),
                FitAssessment.overall_score.label("fit_score"),
            )
            .where(FitAssessment.profile_id == profile_id)
            .ext(distinct_on(FitAssessment.program_id))
            .order_by(
                FitAssessment.program_id,
                FitAssessment.created_at.desc(),
                FitAssessment.id.desc(),
            )
            .subquery()
        )
        query = query.join(
            latest_fit,
            latest_fit.c.fit_program_id == Program.id,
            isouter=True,
        )
        order_by = [
            latest_fit.c.fit_score.desc().nulls_last(),
            Program.canonical_name.asc(),
            Program.id.asc(),
        ]
    elif sort == "deadline":
        earliest_deadline = (
            select(func.min(Intake.application_deadline))
            .where(Intake.program_id == Program.id)
            .correlate(Program)
            .scalar_subquery()
        )
        order_by = [
            earliest_deadline.asc().nulls_last(),
            Program.canonical_name.asc(),
            Program.id.asc(),
        ]

    rows = (
        await session.execute(query.order_by(*order_by).offset(offset).limit(limit))
    ).scalars().all()
    return total, list(rows)


async def get_program_by_id(session: AsyncSession, program_id: uuid.UUID) -> Program | None:
    return await session.get(Program, program_id)


async def get_program_with_institution(
    session: AsyncSession, program_id: uuid.UUID
) -> Program | None:
    program: Program | None = (
        await session.execute(
            select(Program)
            .where(Program.id == program_id)
            .options(selectinload(Program.institution))
        )
    ).scalar_one_or_none()
    return program


async def count_requirements(session: AsyncSession, program_id: uuid.UUID) -> int:
    req_count: int = (
        await session.execute(
            select(func.count())
            .select_from(Requirement)
            .where(Requirement.program_id == program_id)
        )
    ).scalar_one()
    return req_count


async def count_program_evidence(session: AsyncSession, program_id: uuid.UUID) -> int:
    ev_count: int = (
        await session.execute(
            select(func.count())
            .select_from(Evidence)
            .where(Evidence.subject_type == "program", Evidence.subject_id == program_id)
        )
    ).scalar_one()
    return ev_count


async def list_requirements(session: AsyncSession, program_id: uuid.UUID) -> list[Requirement]:
    rows = (
        await session.execute(
            select(Requirement)
            .where(Requirement.program_id == program_id)
            .order_by(Requirement.normalized_key)
        )
    ).scalars().all()
    return list(rows)


async def list_program_evidence_claims(
    session: AsyncSession, program_id: uuid.UUID
) -> list[tuple[str | None, uuid.UUID]]:
    evidence_rows = (
        await session.execute(
            select(Evidence.normalized_claim, Evidence.id).where(
                Evidence.subject_type == "program",
                Evidence.subject_id == program_id,
                Evidence.normalized_claim.is_not(None),
            )
        )
    ).all()
    return [(key, evidence_id) for key, evidence_id in evidence_rows]


async def list_program_evidence(
    session: AsyncSession, program_id: uuid.UUID
) -> list[tuple[Evidence, Source | None]]:
    rows = (
        await session.execute(
            select(Evidence, Source)
            .join(Source, Evidence.source_id == Source.id, isouter=True)
            .where(Evidence.subject_type == "program", Evidence.subject_id == program_id)
            .order_by(Evidence.retrieved_at.desc())
            .limit(100)
        )
    ).all()
    return [(evidence, source) for evidence, source in rows]


async def get_saved_program(
    session: AsyncSession, profile_id: uuid.UUID, program_id: uuid.UUID
) -> SavedProgram | None:
    existing: SavedProgram | None = (
        await session.execute(
            select(SavedProgram).where(
                SavedProgram.profile_id == profile_id, SavedProgram.program_id == program_id
            )
        )
    ).scalar_one_or_none()
    return existing


async def save_program(session: AsyncSession, row: SavedProgram) -> None:
    session.add(row)
    await session.commit()


async def delete_saved_program(session: AsyncSession, row: SavedProgram) -> None:
    await session.delete(row)
    await session.commit()
