"""Synthetic demo student persona (MASTER_SPEC §18).

The persona is a *student*, not a university claim: it may fill profile
fields the user never provided, and it NEVER overwrites user-provided data —
every update targets a field that is empty. University facts come only from
the captured demo fixture; the demo plan itself is marked mode='demo' and
validate_profile reports `persona_applied` so the UI can badge it.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Experience, ProfilePreference, StudentProfile, TestScore
from app.services.onboarding import ENGLISH_TEST_TYPE

# _profile_facts recognises IELTS/IELTS_ACADEMIC as the student's ielts_overall;
# the onboarding service reads ENGLISH_TEST_TYPE. The persona writes both rows
# (same 7.5 score, IELTS dated later so scoring reads it), so either consumer
# finds the demo student's English level.
IELTS_TEST_TYPE = "IELTS"
PERSONA_LABEL = "DEMO PERSONA (MASTER_SPEC §18) — synthetic student, not a real applicant"

PROFILE_UPDATES: dict[str, Any] = {
    "current_degree": "B.Tech Computer Science",
    "field_of_study": "Artificial Intelligence",
    "cgpa": Decimal("8.1"),
    "cgpa_scale": Decimal("10"),
    "total_budget_amount": Decimal("1800000"),
    "budget_currency": "INR",
    "career_goal": "Machine learning engineer",
}
PREFERENCE_UPDATES: dict[str, list[str]] = {
    "preferred_countries": ["DE"],
    "preferred_fields": ["Artificial Intelligence", "Computer Science"],
    "target_intakes": ["Winter 2027"],
}
INTERNSHIP_MONTHS = 12
IELTS_OVERALL = Decimal("7.5")


def persona_profile_updates(profile: StudentProfile) -> dict[str, Any]:
    """Persona values for fields the profile left empty — nothing else."""
    updates: dict[str, Any] = {}
    for key, value in PROFILE_UPDATES.items():
        current = getattr(profile, key)
        if current is None or current == "":
            updates[key] = value
    if not profile.total_experience_months:
        updates["total_experience_months"] = INTERNSHIP_MONTHS
    return updates


def persona_preference_updates(prefs: ProfilePreference) -> dict[str, Any]:
    """Persona values for preference lists the profile left empty."""
    updates: dict[str, Any] = {}
    for key, value in PREFERENCE_UPDATES.items():
        current = getattr(prefs, key)
        if not current:
            updates[key] = list(value)
    return updates


async def _ensure_persona_scores(session: AsyncSession, profile: StudentProfile) -> bool:
    """Add the persona's IELTS 7.5 unless the student already has an English score."""
    existing = (
        await session.execute(
            select(TestScore).where(
                TestScore.profile_id == profile.id,
                TestScore.test_type.in_(
                    (IELTS_TEST_TYPE, "IELTS_ACADEMIC", ENGLISH_TEST_TYPE)
                ),
            )
        )
    ).scalars().first()
    if existing is not None:
        return False
    # Dates are ordered so _profile_facts' "latest test" picks the IELTS row
    # while the onboarding progress screen still finds english_overall.
    session.add(
        TestScore(
            profile_id=profile.id,
            test_type=IELTS_TEST_TYPE,
            overall_score=IELTS_OVERALL,
            test_date=date(2026, 6, 1),
            expiry_date=date(2028, 6, 1),
            status="VALID",
        )
    )
    session.add(
        TestScore(
            profile_id=profile.id,
            test_type=ENGLISH_TEST_TYPE,
            overall_score=IELTS_OVERALL,
            test_date=date(2026, 5, 15),
            status="VALID",
        )
    )
    return True


async def _ensure_internship(session: AsyncSession, profile: StudentProfile) -> bool:
    """Add the persona's one internship unless the student already has experience."""
    existing = (
        await session.execute(
            select(Experience).where(Experience.profile_id == profile.id).limit(1)
        )
    ).scalars().first()
    if existing is not None:
        return False
    session.add(
        Experience(
            profile_id=profile.id,
            experience_type="internship",
            title="Software Engineering Intern",
            organization="Synthetic Labs (demo persona)",
            description=PERSONA_LABEL + ": one 12-month internship on the demo profile.",
            start_date=date(2025, 7, 1),
            end_date=date(2026, 6, 30),
            extra={"demo": True, "label": PERSONA_LABEL, "duration_months": INTERNSHIP_MONTHS},
        )
    )
    return True


async def apply_demo_persona(session: AsyncSession, profile: StudentProfile) -> bool:
    """Fill the MASTER_SPEC §18 persona into empty fields only.

    Returns True when at least one value was filled. User-provided values are
    never touched; existing English scores and experience are left alone.
    """
    changed = False
    for key, value in persona_profile_updates(profile).items():
        setattr(profile, key, value)
        changed = True

    result = await session.execute(
        select(ProfilePreference).where(ProfilePreference.profile_id == profile.id)
    )
    prefs = result.scalars().first()
    if prefs is None:
        prefs = ProfilePreference(profile_id=profile.id)
        session.add(prefs)
        await session.flush()
    for key, value in persona_preference_updates(prefs).items():
        setattr(prefs, key, value)
        changed = True

    if await _ensure_persona_scores(session, profile):
        changed = True
    if await _ensure_internship(session, profile):
        changed = True

    await session.commit()
    return changed
