"""Failure-simulator data access (read-only counterfactual re-scoring).

Backs ``app.services.strategy.simulator``: the simulator keeps the scenario
semantics and the pure re-scoring; the reads feeding the recompute live here
(plus the shared ``strategy_pipeline`` fit/requirement queries).
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.models import ApplicationPlan, Country, Evidence, Program


async def plan_program_ids_for_strategy(
    session: AsyncSession, strategy_run_id: UUID
) -> list[UUID]:
    rows = (
        await session.execute(
            select(ApplicationPlan.program_id).where(
                ApplicationPlan.strategy_run_id == strategy_run_id
            )
        )
    ).scalars()
    return list(rows)


async def programs_with_institution(session: AsyncSession, program_ids: list[UUID]) -> list[Program]:
    rows = (
        await session.execute(
            select(Program)
            .where(Program.id.in_(program_ids))
            .options(selectinload(Program.institution))
        )
    ).scalars().all()
    return list(rows)


async def program_evidence(session: AsyncSession, subject_ids: list[UUID]) -> list[Evidence]:
    rows = (
        await session.execute(
            select(Evidence).where(
                Evidence.subject_type == "program",
                Evidence.subject_id.in_(subject_ids),
            )
        )
    ).scalars().all()
    return list(rows)


async def country_code_by_name(session: AsyncSession, name: str) -> str | None:
    """The ISO code for one country name (ILIKE), or None when unresolvable."""
    code = (
        await session.execute(select(Country.code).where(Country.name.ilike(name)))
    ).scalar_one_or_none()
    return str(code) if code is not None else None
