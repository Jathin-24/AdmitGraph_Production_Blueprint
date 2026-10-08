"""Program browsing, requirement, evidence and save/unsave queries.

Backs the ``app.api.v1.programs`` router; the router keeps the response
shaping (item dicts, pagination cursors, the evidence join rows helper).
"""

import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.models import Evidence, Program, Requirement, SavedProgram, Source


async def list_programs(
    session: AsyncSession,
    profile_id: uuid.UUID,
    saved_only: bool,
    offset: int,
    limit: int,
) -> tuple[int, list[Program]]:
    query = select(Program)
    if saved_only:
        query = query.join(SavedProgram, SavedProgram.program_id == Program.id).where(
            SavedProgram.profile_id == profile_id
        )
    total: int = (
        await session.execute(select(func.count()).select_from(query.subquery()))
    ).scalar_one()
    rows = (
        await session.execute(
            query.order_by(Program.canonical_name).offset(offset).limit(limit)
        )
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
