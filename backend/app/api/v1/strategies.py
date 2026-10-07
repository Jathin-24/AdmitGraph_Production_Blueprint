import uuid
from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.errors import AppError
from app.db.models import (
    ApplicationPlan,
    CounterfactualRun,
    Evidence,
    EvidenceStatus,
    FitAssessment,
    Program,
    Risk,
    RoadmapTask,
    StrategyRun,
)
from app.db.session import get_session
from app.services.profile import get_or_create_profile
from app.services.strategy.simulator import SCENARIOS, apply_scenario

router = APIRouter(tags=["strategies"])

_SEVERITY_RANK = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3}


async def _get_strategy(session: AsyncSession, strategy_id: uuid.UUID) -> StrategyRun:
    strategy = await session.get(StrategyRun, strategy_id)
    if strategy is None:
        raise AppError(404, "NOT_FOUND", "Strategy not found")
    return strategy


async def _portfolio_rows(session: AsyncSession, strategy_id: uuid.UUID) -> list[dict[str, Any]]:
    result = await session.execute(
        select(ApplicationPlan)
        .where(ApplicationPlan.strategy_run_id == strategy_id)
        .order_by(ApplicationPlan.category, ApplicationPlan.priority)
        .options(selectinload(ApplicationPlan.program).selectinload(Program.institution))
    )
    return [
        {
            "program_id": str(p.program_id),
            "program_name": p.program.canonical_name if p.program else None,
            "institution": (
                p.program.institution.canonical_name if p.program and p.program.institution else None
            ),
            "category": p.category.value,
            "priority": p.priority,
            "rationale": p.rationale,
            "next_action": p.next_action,
            "next_deadline": p.next_deadline.isoformat() if p.next_deadline else None,
        }
        for p in result.scalars().all()
    ]


async def _risk_rows(session: AsyncSession, profile_id: uuid.UUID) -> list[dict[str, Any]]:
    result = await session.execute(select(Risk).where(Risk.profile_id == profile_id))
    risks = sorted(
        result.scalars().all(),
        key=lambda r: (_SEVERITY_RANK.get(r.severity.value, 9), r.risk_type),
    )
    return [
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
    ]


async def _task_rows(session: AsyncSession, strategy_id: uuid.UUID) -> list[dict[str, Any]]:
    result = await session.execute(
        select(RoadmapTask)
        .where(RoadmapTask.strategy_run_id == strategy_id)
        .order_by(RoadmapTask.due_date.nullslast())
    )
    return [
        {
            "id": str(t.id),
            "title": t.title,
            "task_type": t.task_type,
            "status": t.status.value,
            "due_date": t.due_date.isoformat() if t.due_date else None,
        }
        for t in result.scalars().all()
    ]


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
    strategy = await _get_strategy(session, strategy_id)
    return {
        "id": str(strategy.id),
        "status": strategy.status.value,
        "plan_health_score": str(strategy.plan_health_score) if strategy.plan_health_score else None,
        "summary": strategy.summary,
        "scoring_version": strategy.scoring_version,
        "strategy_version": strategy.strategy_version,
        "created_at": strategy.created_at.isoformat(),
        "portfolio": await _portfolio_rows(session, strategy_id),
        "risks": await _risk_rows(session, strategy.profile_id),
        "roadmap_tasks": await _task_rows(session, strategy_id),
    }


@router.get("/strategies/{strategy_id}/portfolio")
async def get_portfolio(
    strategy_id: uuid.UUID, session: AsyncSession = Depends(get_session)
) -> dict[str, Any]:
    await _get_strategy(session, strategy_id)
    return {"items": await _portfolio_rows(session, strategy_id)}


@router.get("/strategies/{strategy_id}/risks")
async def get_risks(
    strategy_id: uuid.UUID, session: AsyncSession = Depends(get_session)
) -> dict[str, Any]:
    strategy = await _get_strategy(session, strategy_id)
    return {"items": await _risk_rows(session, strategy.profile_id)}


@router.get("/strategies/{strategy_id}/roadmap")
async def get_roadmap(
    strategy_id: uuid.UUID, session: AsyncSession = Depends(get_session)
) -> dict[str, Any]:
    await _get_strategy(session, strategy_id)
    return {"items": await _task_rows(session, strategy_id)}


@router.get("/strategies/{strategy_id}/evidence-health")
async def get_evidence_health(
    strategy_id: uuid.UUID, session: AsyncSession = Depends(get_session)
) -> dict[str, Any]:
    """Evidence coverage across the programs in this strategy's portfolio."""
    await _get_strategy(session, strategy_id)
    program_ids = (
        (
            await session.execute(
                select(ApplicationPlan.program_id).where(ApplicationPlan.strategy_run_id == strategy_id)
            )
        )
        .scalars()
        .all()
    )
    if not program_ids:
        return {
            "programs_total": 0,
            "programs_with_evidence": 0,
            "evidence_total": 0,
            "by_status": {},
            "by_confidence": {},
            "stale_count": 0,
        }
    rows = (
        await session.execute(
            select(Evidence).where(
                Evidence.subject_type == "program", Evidence.subject_id.in_(program_ids)
            )
        )
    ).scalars().all()
    by_status: dict[str, int] = {}
    by_confidence: dict[str, int] = {}
    covered: set[uuid.UUID] = set()
    from datetime import UTC, datetime

    now = datetime.now(UTC)
    stale = 0
    for e in rows:
        by_status[e.status.value] = by_status.get(e.status.value, 0) + 1
        by_confidence[e.confidence.value] = by_confidence.get(e.confidence.value, 0) + 1
        if e.subject_id:
            covered.add(e.subject_id)
        if e.freshness_deadline is not None and e.freshness_deadline < now:
            stale += 1
    return {
        "programs_total": len(program_ids),
        "programs_with_evidence": len(covered),
        "evidence_total": len(rows),
        "by_status": by_status,
        "by_confidence": by_confidence,
        "stale_count": stale,
        "conflicting_count": by_status.get(EvidenceStatus.CONFLICTING.value, 0),
    }


@router.get("/strategies/{strategy_id}/fit")
async def get_fit_scores(
    strategy_id: uuid.UUID, session: AsyncSession = Depends(get_session)
) -> dict[str, Any]:
    strategy = await _get_strategy(session, strategy_id)
    rows = (
        await session.execute(
            select(FitAssessment)
            .where(
                FitAssessment.profile_id == strategy.profile_id,
                FitAssessment.research_plan_id == strategy.research_plan_id,
            )
            .order_by(FitAssessment.overall_score.desc())
        )
    ).scalars().all()
    return {
        "scoring_version": strategy.scoring_version,
        "items": [
            {
                "program_id": str(f.program_id),
                "overall_score": str(f.overall_score),
                "explanation": f.explanation,
            }
            for f in rows
        ],
    }


@router.post("/strategies/{strategy_id}/export/pdf")
async def export_strategy_pdf(
    strategy_id: uuid.UUID, session: AsyncSession = Depends(get_session)
) -> Any:
    from fastapi.responses import Response

    from app.services.export.pdf import render_strategy_pdf

    strategy = await _get_strategy(session, strategy_id)
    payload = {
        "strategy_id": str(strategy.id),
        "created_at": strategy.created_at.isoformat(),
        "plan_health_score": str(strategy.plan_health_score) if strategy.plan_health_score else None,
        "summary": strategy.summary,
        "scoring_version": strategy.scoring_version,
        "portfolio": await _portfolio_rows(session, strategy_id),
        "risks": await _risk_rows(session, strategy.profile_id),
        "roadmap_tasks": await _task_rows(session, strategy_id),
    }
    pdf_bytes = render_strategy_pdf(payload)
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="admitgraph-strategy-{strategy_id}.pdf"'
        },
    )


@router.post("/strategies/{strategy_id}/simulate")
async def simulate(
    strategy_id: uuid.UUID, payload: dict[str, Any], session: AsyncSession = Depends(get_session)
) -> dict[str, Any]:
    scenario = str(payload.get("scenario", "CUSTOM"))
    if scenario not in SCENARIOS:
        raise AppError(422, "VALIDATION_ERROR", f"Unknown scenario. Allowed: {sorted(SCENARIOS)}")
    strategy = await _get_strategy(session, strategy_id)
    profile = await get_or_create_profile(session)
    base: dict[str, Any] = {
        "total_budget_amount": float(profile.total_budget_amount) if profile.total_budget_amount else None,
        "preferred_countries": [],
    }
    modified = apply_scenario(base, scenario, payload.get("modifications"))
    run = CounterfactualRun(
        profile_id=profile.id,
        base_strategy_run_id=strategy.id,
        scenario_name=scenario,
        modified_profile=modified,
        result={"scenario": scenario, "modified_budget": modified.get("total_budget_amount")},
    )
    session.add(run)
    await session.commit()
    return {"counterfactual_run_id": str(run.id), "scenario": scenario, "modified_profile": modified}
