"""Onboarding answer persistence queries.

Backs ``app.services.onboarding``: the service keeps the schema, the answer
coercion and the upsert/replace rules; the raw reads live here.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    EducationRecord,
    EducationSubject,
    ProfilePreference,
    Skill,
    TestScore,
)


async def english_test_score(
    session: AsyncSession, profile_id: UUID, test_type: str
) -> TestScore | None:
    row = (
        await session.execute(
            select(TestScore).where(
                TestScore.profile_id == profile_id,
                TestScore.test_type == test_type,
            )
        )
    ).scalar_one_or_none()
    return row


async def current_education_record(
    session: AsyncSession, profile_id: UUID
) -> EducationRecord | None:
    """The profile's current (else newest) education record, if any."""
    row = (
        await session.execute(
            select(EducationRecord)
            .where(EducationRecord.profile_id == profile_id)
            .order_by(
                EducationRecord.is_current.is_(True).desc(), EducationRecord.created_at.desc()
            )
            .limit(1)
        )
    ).scalars().first()
    return row


async def education_subjects(
    session: AsyncSession, education_record_id: UUID
) -> list[EducationSubject]:
    rows = (
        await session.execute(
            select(EducationSubject).where(
                EducationSubject.education_record_id == education_record_id
            )
        )
    ).scalars().all()
    return list(rows)


async def skills_by_source(session: AsyncSession, profile_id: UUID, source: str) -> list[Skill]:
    rows = (
        await session.execute(
            select(Skill).where(Skill.profile_id == profile_id, Skill.source == source)
        )
    ).scalars().all()
    return list(rows)


async def profile_skill_names(session: AsyncSession, profile_id: UUID) -> list[str]:
    rows = (
        await session.execute(select(Skill.skill_name).where(Skill.profile_id == profile_id))
    ).scalars().all()
    return list(rows)


async def profile_preference(session: AsyncSession, profile_id: UUID) -> ProfilePreference | None:
    row = (
        await session.execute(
            select(ProfilePreference).where(ProfilePreference.profile_id == profile_id)
        )
    ).scalar_one_or_none()
    return row


async def profile_preference_required(session: AsyncSession, profile_id: UUID) -> ProfilePreference:
    """The profile's preferences row; a missing row is a server error, not None."""
    row = (
        await session.execute(
            select(ProfilePreference).where(ProfilePreference.profile_id == profile_id)
        )
    ).scalar_one()
    return row


async def has_profile_skill(session: AsyncSession, profile_id: UUID) -> bool:
    found = (
        await session.execute(select(Skill.id).where(Skill.profile_id == profile_id).limit(1))
    ).first()
    return found is not None


async def has_scored_english_test(
    session: AsyncSession, profile_id: UUID, test_types_lower: frozenset[str]
) -> bool:
    found = (
        await session.execute(
            select(TestScore.id)
            .where(
                TestScore.profile_id == profile_id,
                # Real test types are stored as reported (IELTS, TOEFL, ...);
                # matching is case-insensitive and a scoreless row (type-only
                # payload) never credits a score the user did not give.
                func.lower(TestScore.test_type).in_(test_types_lower),
                TestScore.overall_score.is_not(None),
            )
            .limit(1)
        )
    ).first()
    return found is not None
