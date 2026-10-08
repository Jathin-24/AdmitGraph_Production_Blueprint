"""Strategy-run, portfolio, risk, roadmap and simulation queries.

Backs the ``app.api.v1.strategies`` router; the router keeps all response
shaping (card dicts, severity sorting) and the profile-scoping 404 decisions.
"""

import uuid
from collections.abc import Collection

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.models import (
    ApplicationPlan,
    CounterfactualRun,
    Evidence,
    FitAssessment,
    ProfilePreference,
    Program,
    Risk,
    RiskStatus,
    RoadmapTask,
    Source,
    SourceAuthority,
    StrategyRun,
    TestScore,
)

# Transaction control for the strategies router (risk transitions, simulations).
from app.db.repositories.base import commit as commit


async def get_strategy_run(session: AsyncSession, strategy_id: uuid.UUID) -> StrategyRun | None:
    return await session.get(StrategyRun, strategy_id)


async def get_portfolio_plans(
    session: AsyncSession, strategy_id: uuid.UUID
) -> list[ApplicationPlan]:
    plans = (
        await session.execute(
            select(ApplicationPlan)
            .where(ApplicationPlan.strategy_run_id == strategy_id)
            .order_by(ApplicationPlan.category, ApplicationPlan.priority)
            .options(
                selectinload(ApplicationPlan.program).selectinload(Program.institution)
            )
        )
    ).scalars().all()
    return list(plans)


async def get_portfolio_fits(
    session: AsyncSession, profile_id: uuid.UUID, program_ids: set[uuid.UUID]
) -> list[FitAssessment]:
    fits = (
        await session.execute(
            select(FitAssessment)
            .where(
                FitAssessment.profile_id == profile_id,
                FitAssessment.program_id.in_(program_ids),
            )
            .order_by(FitAssessment.created_at.desc())
        )
    ).scalars().all()
    return list(fits)


async def get_open_program_risks(
    session: AsyncSession, profile_id: uuid.UUID, program_ids: set[uuid.UUID]
) -> list[Risk]:
    open_risks = (
        await session.execute(
            select(Risk).where(
                Risk.profile_id == profile_id,
                Risk.program_id.in_(program_ids),
                Risk.status == RiskStatus.OPEN,
            )
        )
    ).scalars().all()
    return list(open_risks)


async def get_program_evidence(
    session: AsyncSession, program_ids: Collection[uuid.UUID]
) -> list[Evidence]:
    evidence_rows = (
        await session.execute(
            select(Evidence).where(
                Evidence.subject_type == "program", Evidence.subject_id.in_(program_ids)
            )
        )
    ).scalars().all()
    return list(evidence_rows)


async def get_profile_risks(session: AsyncSession, profile_id: uuid.UUID) -> list[Risk]:
    result = await session.execute(select(Risk).where(Risk.profile_id == profile_id))
    return list(result.scalars().all())


async def get_roadmap_tasks(session: AsyncSession, strategy_id: uuid.UUID) -> list[RoadmapTask]:
    result = await session.execute(
        select(RoadmapTask)
        .where(RoadmapTask.strategy_run_id == strategy_id)
        .order_by(RoadmapTask.due_date.nullslast())
    )
    return list(result.scalars().all())


async def list_strategy_runs(session: AsyncSession, profile_id: uuid.UUID) -> list[StrategyRun]:
    result = await session.execute(
        select(StrategyRun)
        .where(StrategyRun.profile_id == profile_id)
        .order_by(StrategyRun.created_at.desc())
    )
    return list(result.scalars().all())


async def get_risk(session: AsyncSession, risk_id: uuid.UUID) -> Risk | None:
    return await session.get(Risk, risk_id)


async def get_portfolio_program_ids(
    session: AsyncSession, strategy_id: uuid.UUID
) -> list[uuid.UUID]:
    program_ids = (
        await session.execute(
            select(ApplicationPlan.program_id).where(
                ApplicationPlan.strategy_run_id == strategy_id
            )
        )
    ).scalars().all()
    return list(program_ids)


async def get_source_authorities(
    session: AsyncSession, source_ids: set[uuid.UUID]
) -> list[tuple[uuid.UUID, SourceAuthority]]:
    rows = (
        await session.execute(
            select(Source.id, Source.source_authority).where(Source.id.in_(source_ids))
        )
    ).all()
    return [(source_id, authority) for source_id, authority in rows]


async def get_strategy_fits(
    session: AsyncSession, profile_id: uuid.UUID, research_plan_id: uuid.UUID | None
) -> list[FitAssessment]:
    rows = (
        await session.execute(
            select(FitAssessment)
            .where(
                FitAssessment.profile_id == profile_id,
                FitAssessment.research_plan_id == research_plan_id,
            )
            .order_by(FitAssessment.overall_score.desc())
        )
    ).scalars().all()
    return list(rows)


async def get_profile_preferences(
    session: AsyncSession, profile_id: uuid.UUID
) -> ProfilePreference | None:
    prefs: ProfilePreference | None = (
        await session.execute(
            select(ProfilePreference).where(ProfilePreference.profile_id == profile_id)
        )
    ).scalar_one_or_none()
    return prefs


async def get_latest_english_score(
    session: AsyncSession, profile_id: uuid.UUID
) -> TestScore | None:
    english: TestScore | None = (
        await session.execute(
            select(TestScore)
            .where(
                TestScore.profile_id == profile_id,
                TestScore.test_type.in_(("english_overall", "IELTS", "IELTS_ACADEMIC")),
            )
            .order_by(TestScore.created_at.desc())
        )
    ).scalars().first()
    return english


async def list_strategy_plans(
    session: AsyncSession, strategy_id: uuid.UUID
) -> list[ApplicationPlan]:
    plans = (
        await session.execute(
            select(ApplicationPlan).where(ApplicationPlan.strategy_run_id == strategy_id)
        )
    ).scalars().all()
    return list(plans)


async def save_counterfactual_run(session: AsyncSession, run: CounterfactualRun) -> None:
    session.add(run)
    await session.commit()
