from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.repositories import profile as profile_repo
from app.db.session import get_session
from app.schemas.profile import (
    CompletionOut,
    ProfileCreate,
    ProfileOut,
    ProfileUpdate,
    ValidationOut,
)
from app.services import profile as profile_service

router = APIRouter(tags=["profile"])


@router.get("/me/profile", response_model=ProfileOut)
async def get_profile(session: AsyncSession = Depends(get_session)) -> ProfileOut:
    profile = await profile_service.get_or_create_profile(session)
    await profile_repo.commit(session)
    return ProfileOut.model_validate(profile)


@router.post("/me/profile", response_model=ProfileOut)
async def create_profile(
    payload: ProfileCreate, session: AsyncSession = Depends(get_session)
) -> ProfileOut:
    profile = await profile_service.upsert_profile(session, payload)
    return ProfileOut.model_validate(profile)


@router.patch("/me/profile", response_model=ProfileOut)
async def patch_profile(
    payload: ProfileUpdate, session: AsyncSession = Depends(get_session)
) -> ProfileOut:
    profile = await profile_service.upsert_profile(session, payload)
    return ProfileOut.model_validate(profile)


@router.get("/me/profile/completion", response_model=CompletionOut)
async def completion(session: AsyncSession = Depends(get_session)) -> CompletionOut:
    profile = await profile_service.get_or_create_profile(session)
    percent, missing = profile_service.compute_completion(profile)
    return CompletionOut(profile_completion=percent, missing_fields=missing)


@router.post("/me/profile/validate", response_model=ValidationOut)
async def validate(session: AsyncSession = Depends(get_session)) -> ValidationOut:
    profile = await profile_service.get_or_create_profile(session)
    return profile_service.validate_profile(profile)
