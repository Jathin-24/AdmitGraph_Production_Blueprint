"""Profile business logic: completion, validation, creation."""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import ProfilePreference, StudentProfile, User
from app.schemas.profile import ProfileCreate, ProfileUpdate, ValidationIssue, ValidationOut

TRACKED_FIELDS = [
    "current_degree",
    "field_of_study",
    "institution_name",
    "graduation_year",
    "cgpa",
    "total_budget_amount",
    "career_goal",
]


async def get_or_create_default_user(session: AsyncSession) -> User:
    """MVP stand-in for OAuth: single local student user. Documented in ENVIRONMENT.md."""
    result = await session.execute(select(User).order_by(User.created_at).limit(1))
    user = result.scalar_one_or_none()
    if user is not None:
        return user
    user = User(email="demo@admitgraph.local", full_name="Demo Student")
    session.add(user)
    await session.flush()
    return user


async def get_or_create_profile(session: AsyncSession) -> StudentProfile:
    user = await get_or_create_default_user(session)
    result = await session.execute(select(StudentProfile).where(StudentProfile.user_id == user.id))
    profile = result.scalar_one_or_none()
    if profile is not None:
        return profile
    profile = StudentProfile(user_id=user.id)
    session.add(profile)
    await session.flush()
    session.add(ProfilePreference(profile_id=profile.id))
    await session.flush()
    return profile


def compute_completion(profile: StudentProfile) -> tuple[float, list[str]]:
    missing: list[str] = []
    for f in TRACKED_FIELDS:
        value = getattr(profile, f)
        if value is None or value == "":
            missing.append(f)
    filled = len(TRACKED_FIELDS) - len(missing)
    return round(100.0 * filled / len(TRACKED_FIELDS), 2), missing


def validate_profile(profile: StudentProfile) -> ValidationOut:
    issues: list[ValidationIssue] = []
    if profile.cgpa is not None and profile.cgpa_scale is not None:
        if profile.cgpa > profile.cgpa_scale:
            issues.append(ValidationIssue(field="cgpa", message="CGPA cannot exceed the scale"))
    if profile.percentage is not None and not (0 <= profile.percentage <= 100):
        issues.append(ValidationIssue(field="percentage", message="Percentage must be between 0 and 100"))
    if profile.graduation_year is not None and not (1950 <= profile.graduation_year <= 2100):
        issues.append(ValidationIssue(field="graduation_year", message="Unrealistic graduation year"))
    if profile.total_budget_amount is not None and profile.total_budget_amount < 0:
        issues.append(ValidationIssue(field="total_budget_amount", message="Budget cannot be negative"))
    if profile.cgpa is None and profile.percentage is None:
        issues.append(
            ValidationIssue(
                field="cgpa", message="Provide either CGPA or percentage; otherwise matching is UNKNOWN"
            )
        )
    return ValidationOut(valid=len(issues) == 0, issues=issues)


async def upsert_profile(session: AsyncSession, payload: ProfileCreate | ProfileUpdate) -> StudentProfile:
    profile = await get_or_create_profile(session)
    for field_name, value in payload.model_dump(exclude_unset=True).items():
        if hasattr(profile, field_name):
            setattr(profile, field_name, value)
    completion, _ = compute_completion(profile)
    profile.profile_completion = Decimal(str(completion))
    await session.commit()
    await session.refresh(profile)
    return profile
