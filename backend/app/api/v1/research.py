import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Header
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError
from app.db.models import ResearchPlan, ResearchPlanStep
from app.db.session import get_session
from app.schemas.research import (
    ResearchEventsOut,
    ResearchPlanCreate,
    ResearchPlanDetail,
    ResearchPlanOut,
    ResearchRunCreate,
    ResearchStepOut,
)
from app.services.profile import get_or_create_profile
from app.services.research.orchestrator import ResearchService, dispatch_plan

router = APIRouter(tags=["research"])


async def _get_plan_or_404(session: AsyncSession, run_id: uuid.UUID) -> ResearchPlan:
    plan = await session.get(ResearchPlan, run_id)
    if plan is None:
        raise AppError(404, "NOT_FOUND", "Research run not found")
    return plan


@router.post("/research/plan", response_model=ResearchPlanOut)
async def create_plan(
    payload: ResearchPlanCreate, session: AsyncSession = Depends(get_session)
) -> ResearchPlanOut:
    profile = await get_or_create_profile(session)
    goal = {**payload.goal, "intake_year": payload.intake_year or datetime.now(UTC).year + 1}
    service = ResearchService(session=session)
    plan = await service.create_plan(session, profile.id, goal)
    return ResearchPlanOut(research_plan_id=plan.id, status=plan.status.value)


@router.post("/research/runs", response_model=ResearchPlanOut)
async def create_run(
    payload: ResearchRunCreate,
    session: AsyncSession = Depends(get_session),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> ResearchPlanOut:
    profile = await get_or_create_profile(session)
    goal = {**payload.goal, "intake_year": payload.intake_year or datetime.now(UTC).year + 1}
    service = ResearchService(session=session)
    plan = await service.create_plan(session, profile.id, goal, idempotency_key=idempotency_key)
    if plan.status.value == "QUEUED":
        dispatch_plan(plan.id)
    return ResearchPlanOut(research_plan_id=plan.id, status=plan.status.value)


@router.get("/research/runs/{run_id}", response_model=ResearchPlanDetail)
async def get_run(run_id: uuid.UUID, session: AsyncSession = Depends(get_session)) -> ResearchPlanDetail:
    plan = await _get_plan_or_404(session, run_id)
    return ResearchPlanDetail(
        id=plan.id,
        status=plan.status.value,
        planned_queries=[
            q["q"] if isinstance(q, dict) else str(q) for q in (plan.planned_queries or [])
        ],
        error_message=plan.error_message,
        created_at=plan.created_at,
        started_at=plan.started_at,
        completed_at=plan.completed_at,
    )


@router.get("/research/runs/{run_id}/events", response_model=ResearchEventsOut)
async def run_events(run_id: uuid.UUID, session: AsyncSession = Depends(get_session)) -> ResearchEventsOut:
    await _get_plan_or_404(session, run_id)
    result = await session.execute(
        select(ResearchPlanStep).where(ResearchPlanStep.research_plan_id == run_id)
    )
    steps = [
        ResearchStepOut(
            step_key=s.step_key,
            service_name=s.service_name,
            status=s.status.value,
            error_message=s.error_message,
            output=s.output,
        )
        for s in result.scalars().all()
    ]
    return ResearchEventsOut(run_id=run_id, steps=steps)


@router.post("/research/runs/{run_id}/cancel")
async def cancel_run(run_id: uuid.UUID, session: AsyncSession = Depends(get_session)) -> dict[str, str]:
    plan = await _get_plan_or_404(session, run_id)
    if plan.status.value in ("QUEUED", "RUNNING"):
        from app.db.models import RunStatus

        plan.status = RunStatus.CANCELLED
        await session.commit()
    return {"status": plan.status.value}
