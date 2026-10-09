"""Profile business logic: completion, validation, creation."""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError
from app.db.models import ProfilePreference, StudentProfile, User, UserRole
from app.db.repositories.profile import get_profile_by_user, get_user_by_email
from app.schemas.profile import ProfileCreate, ProfileUpdate, ValidationIssue, ValidationOut

DEMO_EMAIL = "demo@admitgraph.local"

TRACKED_FIELDS = [
    "current_degree",
    "field_of_study",
    "institution_name",
    "graduation_year",
    "cgpa",
    "total_budget_amount",
    "career_goal",
]


async def get_or_create_default_user(session: AsyncSession, *, email: str = DEMO_EMAIL) -> User:
    """Resolve the requesting user: authenticated (ContextVar) or local demo user.

    The auth middleware decodes the bearer token into a ContextVar once per
    request. Then:

    - Signed-in caller whose account no longer resolves (deleted account behind
      a still-valid token) → AppError 401, so a stale session gets a clean 401
      instead of silently reading the shared demo profile.
    - No Authorization header at all (or a direct service call in tests) → the
      stable local demo user. That anonymous demo fallback is a deliberate
      product feature (demo mode) and must keep working for reads AND writes.
    - Anonymous traffic always maps to the dedicated demo account — never to
      whichever real account happened to register first — so signed-out
      sessions cannot read a student's data.
    Documented in deployment/ENVIRONMENT.md.
    """
    from app.core.security import current_user_id

    uid = current_user_id()
    if uid is not None:
        user = await session.get(User, uid)
        if user is None:
            # Valid token, missing account: the session is stale. Serving the
            # demo profile here would leak shared data into a signed-in flow.
            raise AppError(401, "UNAUTHENTICATED", "Account no longer exists")
        return user
    demo = await get_user_by_email(session, email)
    if demo is not None:
        return demo
    # Fresh database (or missing seed): bootstrap the local demo account
    # (passwordless, STUDENT — a shared admin-capable demo account is how
    # anonymous callers used to reach /admin/*; admin.py now requires a
    # verified ADMIN token and never consults this row's role). Note that
    # scripts/seed_demo.py (owned by another workstream) may still promote
    # this row to ADMIN for local exploration — harmless now that admin
    # gating is independent of the demo role. Never fall back to the oldest
    # registered participant — anonymous traffic must not read a real
    # student's data.
    user = User(email=email, full_name="Demo Student", role=UserRole.STUDENT)
    session.add(user)
    await session.flush()
    return user


async def get_or_create_profile(session: AsyncSession) -> StudentProfile:
    user = await get_or_create_default_user(session)
    profile = await get_profile_by_user(session, user.id)
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
