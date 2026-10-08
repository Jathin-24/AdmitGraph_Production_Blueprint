from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_session
from app.schemas.onboarding import (
    OnboardingAnswersIn,
    OnboardingProgressOut,
    OnboardingSchema,
)
from app.services import onboarding as onboarding_service

router = APIRouter(tags=["onboarding"])


@router.get("/onboarding/schema", response_model=OnboardingSchema)
async def schema() -> OnboardingSchema:
    return onboarding_service.ONBOARDING_SCHEMA


@router.post("/onboarding/answers")
async def answers(
    payload: OnboardingAnswersIn, session: AsyncSession = Depends(get_session)
) -> dict[str, object]:
    filled = await onboarding_service.apply_answers(session, payload.answers)
    return {"accepted_keys": sorted(filled)}


@router.get("/onboarding/progress", response_model=OnboardingProgressOut)
async def progress(session: AsyncSession = Depends(get_session)) -> OnboardingProgressOut:
    """Completion for the current profile.

    REQUIRED_KEYS (field_of_study, current_degree, cgpa, total_budget_amount
    — the four original questions: goal/education/tests/budget) still decide
    `missing_required_keys`; optional answers only ever raise
    `completion_percent`.
    """
    filled = await onboarding_service.answered_from_db(session)
    answered = sorted(filled)
    missing_required = sorted(onboarding_service.REQUIRED_KEYS - filled)
    total = len(onboarding_service.REQUIRED_KEYS | filled)
    percent = round(100.0 * len(filled) / total, 2) if total else 0.0
    return OnboardingProgressOut(
        completion_percent=percent, answered_keys=answered, missing_required_keys=missing_required
    )
