import uuid
from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import CounterfactualRun, StrategyRun
from app.db.session import get_session
from app.services.profile import get_or_create_profile
from app.services.strategy.simulator import SCENARIOS, apply_scenario

router = APIRouter(tags=["strategies"])


@router.get("/strategies")
async def list_strategies(session: AsyncSession = Depends(get_session)) -> dict[str, Any]:
    result = await session.execute(select(StrategyRun).order_by(StrategyRun.created_at.desc()))
    return {
        "items": [
            {
                "id": str(s.id),
                "status": s.status.value,
                "plan_health_score": str(s.plan_health_score) if s.plan_health_score else None,
                "summary": s.summary,
                "created_at": s.created_at.isoformat(),
            }
            for s in result.scalars().all()
        ]
    }


@router.get("/strategies/{strategy_id}")
async def get_strategy(
    strategy_id: uuid.UUID, session: AsyncSession = Depends(get_session)
) -> dict[str, Any]:
    from sqlalchemy.orm import selectinload

    from app.db.models import ApplicationPlan, Program, Risk, RoadmapTask

    strategy = await session.get(StrategyRun, strategy_id)
    if strategy is None:
        return {"error": "Strategy not found"}

    plans_result = await session.execute(
        select(ApplicationPlan)
        .where(ApplicationPlan.strategy_run_id == strategy_id)
        .order_by(ApplicationPlan.category, ApplicationPlan.priority)
        .options(selectinload(ApplicationPlan.program).selectinload(Program.institution))
    )
    risks_result = await session.execute(
        select(Risk).where(Risk.profile_id == strategy.profile_id)
    )
    severity_rank = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3}
    risks = sorted(
        risks_result.scalars().all(),
        key=lambda r: (severity_rank.get(r.severity.value, 9), r.risk_type),
    )
    tasks_result = await session.execute(
        select(RoadmapTask)
        .where(RoadmapTask.strategy_run_id == strategy_id)
        .order_by(RoadmapTask.due_date.nullslast())
    )

    return {
        "id": str(strategy.id),
        "status": strategy.status.value,
        "plan_health_score": str(strategy.plan_health_score) if strategy.plan_health_score else None,
        "summary": strategy.summary,
        "scoring_version": strategy.scoring_version,
        "strategy_version": strategy.strategy_version,
        "created_at": strategy.created_at.isoformat(),
        "portfolio": [
            {
                "program_id": str(p.program_id),
                "program_name": p.program.canonical_name if p.program else None,
                "institution": (
                    p.program.institution.canonical_name
                    if p.program and p.program.institution
                    else None
                ),
                "category": p.category.value,
                "priority": p.priority,
                "rationale": p.rationale,
                "next_action": p.next_action,
                "next_deadline": p.next_deadline.isoformat() if p.next_deadline else None,
            }
            for p in plans_result.scalars().all()
        ],
        "risks": [
            {
                "id": str(r.id),
                "risk_type": r.risk_type,
                "severity": r.severity.value,
                "title": r.title,
                "reason": r.reason,
                "recommended_action": r.recommended_action,
                "status": r.status.value,
            }
            for r in risks
        ],
        "roadmap_tasks": [
            {
                "id": str(t.id),
                "title": t.title,
                "task_type": t.task_type,
                "status": t.status.value,
                "due_date": t.due_date.isoformat() if t.due_date else None,
            }
            for t in tasks_result.scalars().all()
        ],
    }


@router.post("/strategies/{strategy_id}/simulate")
async def simulate(
    strategy_id: uuid.UUID, payload: dict[str, Any], session: AsyncSession = Depends(get_session)
) -> dict[str, Any]:
    scenario = str(payload.get("scenario", "CUSTOM"))
    if scenario not in SCENARIOS:
        return {"error": f"Unknown scenario. Allowed: {sorted(SCENARIOS)}"}
    strategy = await session.get(StrategyRun, strategy_id)
    if strategy is None:
        return {"error": "Strategy not found"}
    profile = await get_or_create_profile(session)
    base: dict[str, Any] = {
        "total_budget_amount": float(profile.total_budget_amount) if profile.total_budget_amount else None,
        "preferred_countries": [],
    }
    modified = apply_scenario(base, scenario, payload.get("modifications"))
    run = CounterfactualRun(
        profile_id=profile.id,
        base_strategy_run_id=strategy_id,
        scenario_name=scenario,
        modified_profile=modified,
        result={"scenario": scenario, "modified_budget": modified.get("total_budget_amount")},
    )
    session.add(run)
    await session.commit()
    return {"counterfactual_run_id": str(run.id), "scenario": scenario, "modified_profile": modified}
