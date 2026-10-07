from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import ProfilePreference
from app.db.session import get_session
from app.schemas.onboarding import (
    OnboardingAnswersIn,
    OnboardingProgressOut,
    OnboardingSchema,
)
from app.services import onboarding as onboarding_service
from app.services.profile import TRACKED_FIELDS, get_or_create_profile

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
    profile = await get_or_create_profile(session)
    filled = {f for f in TRACKED_FIELDS if getattr(profile, f) not in (None, "")}
    result = await session.execute(
        select(ProfilePreference).where(ProfilePreference.profile_id == profile.id)
    )
    prefs = result.scalar_one()
    if prefs.preferred_countries:
        filled.add("preferred_countries")
    if prefs.target_intakes:
        filled.add("target_intakes")
    answered = sorted(filled)
    missing_required = sorted(onboarding_service.REQUIRED_KEYS - filled)
    total = len(onboarding_service.REQUIRED_KEYS | filled)
    percent = round(100.0 * len(filled) / total, 2) if total else 0.0
    return OnboardingProgressOut(
        completion_percent=percent, answered_keys=answered, missing_required_keys=missing_required
    )
