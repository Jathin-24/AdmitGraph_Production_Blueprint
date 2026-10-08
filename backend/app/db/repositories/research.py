"""Research plan and step queries.

Backs the ``app.api.v1.research`` router; the router keeps the run-level
profile-scoping 404 and the cancel transition.
"""

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import ResearchPlan, ResearchPlanStep

# Transaction control for the research router (cancel_run).
from app.db.repositories.base import commit as commit


async def get_plan(session: AsyncSession, run_id: uuid.UUID) -> ResearchPlan | None:
    return await session.get(ResearchPlan, run_id)


async def list_plan_steps(session: AsyncSession, run_id: uuid.UUID) -> list[ResearchPlanStep]:
    result = await session.execute(
        select(ResearchPlanStep).where(ResearchPlanStep.research_plan_id == run_id)
    )
    return list(result.scalars().all())
