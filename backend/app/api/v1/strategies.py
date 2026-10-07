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
