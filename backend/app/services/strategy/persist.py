"""Persistence helpers for strategy pipeline steps."""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    ApplicationPlan,
    FitAssessment,
    Program,
    Requirement,
    Risk,
    RiskStatus,
    RoadmapTask,
    RunStatus,
    StrategyRun,
    TaskStatus,
)
from app.services.matching.evaluator import ProfileFacts, evaluate
from app.services.risk.engine import RiskContext, assess
from app.services.scoring.scorer import SCORING_VERSION, DimensionInput, overall_score
from app.services.strategy.health import plan_health_score
from app.services.strategy.portfolio import Candidate, build_portfolio

STRATEGY_VERSION = "v1"


async def step_evaluate_requirements(session: AsyncSession, profile_id: uuid.UUID) -> dict[str, Any]:
    programs = (await session.execute(select(Program).limit(10))).scalars().all()
    evaluated = 0
    for program in programs:
        reqs = (
            await session.execute(select(Requirement).where(Requirement.program_id == program.id))
        ).scalars().all()
        for req in reqs:
            facts = await _profile_facts(session, profile_id)
            status, reason = evaluate(
                {
                    "normalized_key": req.normalized_key,
                    "operator": req.operator or "",
                    "value": req.value,
                },
                facts,
            )
            req.status = status
            evaluated += 1
    await session.commit()
    return {"requirements_evaluated": evaluated}


async def step_score_fit(
    session: AsyncSession, profile_id: uuid.UUID, research_plan_id: uuid.UUID | None
) -> dict[str, Any]:
    programs = (await session.execute(select(Program).limit(10))).scalars().all()
    created = 0
    for program in programs:
        reqs = (
            await session.execute(select(Requirement).where(Requirement.program_id == program.id))
        ).scalars().all()
        dims: list[DimensionInput] = [
            DimensionInput(
                dimension="academic",
                statuses=[r.status for r in reqs if r.requirement_type in ("academic", "cgpa")] or [],
            ),
            DimensionInput(
                dimension="prerequisites",
                statuses=[r.status for r in reqs if r.requirement_type in ("prerequisite", "subjects")],
            ),
            DimensionInput(
                dimension="language",
                statuses=[r.status for r in reqs if r.requirement_type in ("language", "test")],
            ),
            DimensionInput(
                dimension="financial",
                statuses=[r.status for r in reqs if r.requirement_type in ("tuition", "fees", "budget")],
            ),
        ]
        dims_with_data = [d for d in dims if d.statuses]
        score, subscores = overall_score(dims_with_data or [DimensionInput("evidence_confidence", [])])
        if not dims_with_data:
            # No requirement evidence yet -> unknown, honest score near neutral-low.
            score = Decimal("25")
        session.add(
            FitAssessment(
                research_plan_id=research_plan_id,
                profile_id=profile_id,
                program_id=program.id,
                overall_score=score,
                scoring_version=SCORING_VERSION,
                explanation=f"subscores={ {k: str(v) for k, v in subscores.items()} }",
            )
        )
        created += 1
    await session.commit()
    return {"fit_assessments_created": created}


async def step_assess_risks(session: AsyncSession, profile_id: uuid.UUID) -> dict[str, Any]:
    reqs = (await session.execute(select(Requirement))).scalars().all()
    eligibility = [(r.normalized_key, r.status, r.mandatory) for r in reqs]
    risks = assess(RiskContext(eligibility=eligibility))
    created = 0
    for risk in risks:
        session.add(
            Risk(
                profile_id=profile_id,
                program_id=None,
                risk_type=risk.risk_type,
                severity=risk.severity,
                title=risk.title,
                reason=risk.reason,
                recommended_action=risk.recommended_action,
                status=RiskStatus.OPEN,
            )
        )
        created += 1
    await session.commit()
    return {"risks_created": created}


async def step_build_strategy(
    session: AsyncSession, profile_id: uuid.UUID, research_plan_id: uuid.UUID | None
) -> dict[str, Any]:
    fits = (
        await session.execute(
            select(FitAssessment)
            .where(FitAssessment.profile_id == profile_id)
            .order_by(FitAssessment.overall_score.desc())
        )
    ).scalars().all()
    candidates = [Candidate(program_id=str(f.program_id), fit_score=f.overall_score) for f in fits]
    portfolio = build_portfolio(candidates)
    run = StrategyRun(
        profile_id=profile_id,
        research_plan_id=research_plan_id,
        status=RunStatus.SUCCEEDED,
        scoring_version=SCORING_VERSION,
        strategy_version=STRATEGY_VERSION,
        summary=f"{len(candidates)} programs scored",
        plan_health_score=plan_health_score(
            blocker_count=0,
            unresolved_conflicts=0,
            stale_evidence_count=0,
            nearest_deadline=None,
            financial_feasible=True,
            documents_ready=True,
        ),
    )
    session.add(run)
    await session.flush()
    for category, items in portfolio.items():
        for i, cand in enumerate(items):
            session.add(
                ApplicationPlan(
                    strategy_run_id=run.id,
                    profile_id=profile_id,
                    program_id=uuid.UUID(cand.program_id),
                    category=category,
                    priority=i + 1,
                    rationale=f"Fit score {cand.fit_score}",
                )
            )
    session.add(
        RoadmapTask(
            strategy_run_id=run.id,
            profile_id=profile_id,
            title="Review top program requirements",
            task_type="VERIFY",
            status=TaskStatus.TODO,
        )
    )
    await session.commit()
    return {"strategy_run_id": str(run.id), "total_candidates": len(candidates)}


async def _profile_facts(session: AsyncSession, profile_id: uuid.UUID) -> ProfileFacts:
    from app.db.models import StudentProfile, TestScore

    profile = await session.get(StudentProfile, profile_id)
    facts = ProfileFacts()
    if profile is None:
        return facts
    facts.cgpa = profile.cgpa
    facts.cgpa_scale = profile.cgpa_scale
    facts.percentage = profile.percentage
    facts.backlogs = profile.backlogs or 0
    facts.total_budget_amount = profile.total_budget_amount
    facts.budget_currency = profile.budget_currency
    facts.graduation_year = profile.graduation_year
    tests = (
        await session.execute(
            select(TestScore)
            .where(TestScore.profile_id == profile_id)
            .order_by(TestScore.test_date.desc())
            .limit(1)
        )
    ).scalars().first()
    if tests is not None:
        if tests.test_type.upper() in ("IELTS", "IELTS_ACADEMIC"):
            facts.ielts_overall = tests.overall_score
        facts.english_test_type = tests.test_type
    return facts
