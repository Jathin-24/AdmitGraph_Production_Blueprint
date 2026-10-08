"""Admin/observability aggregates (search usage, research run listings).

Backs the ``app.api.v1.admin`` router; the router keeps the derived response
fields (averages, failure/partial counts, pagination cursors).
"""

import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import ResearchPlan, ResearchPlanStep, RunStatus, SearchRun


async def count_search_runs(session: AsyncSession) -> int:
    total: int = (await session.execute(select(func.count()).select_from(SearchRun))).scalar_one()
    return total


async def search_runs_by_engine(session: AsyncSession) -> list[tuple[str, int]]:
    by_engine_rows = (
        await session.execute(
            select(SearchRun.engine, func.count())
            .group_by(SearchRun.engine)
            .order_by(SearchRun.engine)
        )
    ).all()
    return [(engine, count) for engine, count in by_engine_rows]


async def search_runs_by_status(session: AsyncSession) -> list[tuple[RunStatus, int]]:
    by_status_rows = (
        await session.execute(
            select(SearchRun.status, func.count())
            .group_by(SearchRun.status)
            .order_by(SearchRun.status)
        )
    ).all()
    return [(status, count) for status, count in by_status_rows]


async def search_usage_totals(session: AsyncSession) -> tuple[int, int | None, int | None, int]:
    searches, results, duration_ms, cache_hits = (
        await session.execute(
            select(
                func.count(SearchRun.id),
                func.coalesce(func.sum(SearchRun.result_count), 0),
                func.coalesce(func.sum(SearchRun.duration_ms), 0),
                func.coalesce(func.count(SearchRun.id).filter(SearchRun.cache_hit.is_(True)), 0),
            )
        )
    ).one()
    return searches, results, duration_ms, cache_hits


async def count_research_plans(session: AsyncSession) -> int:
    total: int = (
        await session.execute(select(func.count()).select_from(ResearchPlan))
    ).scalar_one()
    return total


async def list_research_plans(
    session: AsyncSession, offset: int, limit: int
) -> list[ResearchPlan]:
    plans = (
        await session.execute(
            select(ResearchPlan)
            .order_by(ResearchPlan.created_at.desc())
            .offset(offset)
            .limit(limit)
        )
    ).scalars().all()
    return list(plans)


async def list_plan_steps(
    session: AsyncSession, research_plan_id: uuid.UUID
) -> list[ResearchPlanStep]:
    steps = (
        await session.execute(
            select(ResearchPlanStep).where(
                ResearchPlanStep.research_plan_id == research_plan_id
            )
        )
    ).scalars().all()
    return list(steps)
