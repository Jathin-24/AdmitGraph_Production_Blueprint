"""`python -m scripts.seed_demo` seeds a STUDENT demo account, idempotently.

Regression for P0-2 (audit): the seeder used to promote `demo@admitgraph.local`
to ADMIN, so anonymous/demo traffic inherited an admin-capable row. The seeder
now asserts STUDENT on every run (local admin access is the opt-in
`python -m scripts.grant_demo_admin`), fills the MASTER_SPEC §18 persona into
empty fields only, and creates its labeled evidence fixture at most once.

Integration test against real PostgreSQL (fixture: tests/conftest.py).
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from sqlalchemy import delete, func, select

from app.db.models import (
    Evidence,
    Experience,
    ProfilePreference,
    Source,
    StudentProfile,
    User,
    UserRole,
)
from app.db.models import (
    TestScore as StudentTestScore,  # aliased: pytest must not collect the model
)
from scripts.seed_demo import DEMO_EMAIL, DEMO_LABEL
from scripts.seed_demo import main as run_seed

DEMO_SOURCE_URL = "https://example.edu/synthetic-msc-ai"


async def _demo_user(session: Any) -> User:
    user = (
        (await session.execute(select(User).where(User.email == DEMO_EMAIL)))
        .scalars()
        .one_or_none()
    )
    assert user is not None, "seed_demo must create the demo account"
    return user


async def _demo_profile(session: Any, user_id: Any) -> StudentProfile:
    return (
        (
            await session.execute(
                select(StudentProfile).where(StudentProfile.user_id == user_id)
            )
        )
        .scalars()
        .one()
    )


async def _count(session: Any, model: Any, *conditions: Any) -> int:
    query = select(func.count(model.id))
    if conditions:
        query = query.where(*conditions)
    return int((await session.execute(query)).scalar_one())


async def _row_totals(session: Any) -> dict[str, int]:
    """Row counts that a second seed run must not move (idempotency)."""
    user = await _demo_user(session)
    profile = await _demo_profile(session, user.id)
    return {
        "users": await _count(session, User, User.email == DEMO_EMAIL),
        "profiles": await _count(
            session, StudentProfile, StudentProfile.user_id == user.id
        ),
        "preferences": await _count(
            session, ProfilePreference, ProfilePreference.profile_id == profile.id
        ),
        "scores": await _count(
            session, StudentTestScore, StudentTestScore.profile_id == profile.id
        ),
        "experiences": await _count(
            session, Experience, Experience.profile_id == profile.id
        ),
        "sources": await _count(session, Source, Source.canonical_url == DEMO_SOURCE_URL),
        "labeled_evidence": await _count(
            session, Evidence, Evidence.claim.like(f"{DEMO_LABEL}%")
        ),
    }


async def test_seed_demo_fills_the_persona_and_creates_a_student(db_session: Any) -> None:
    # Simulate a fresh database: empty the fields the persona is allowed to fill
    # (the seeder must never overwrite user-provided values, so a run over an
    # already-populated profile would prove nothing about a first seed).
    existing = (
        (await db_session.execute(select(User).where(User.email == DEMO_EMAIL)))
        .scalars()
        .one_or_none()
    )
    if existing is not None:
        profile = await _demo_profile(db_session, existing.id)
        for field in (
            "current_degree",
            "field_of_study",
            "cgpa",
            "cgpa_scale",
            "total_budget_amount",
            "budget_currency",
            "career_goal",
        ):
            setattr(profile, field, None)
        profile.total_experience_months = 0
        # Persona rows are only "ensured when missing", so earlier suites that
        # left scores/experience behind would hide a first-run seed entirely.
        await db_session.execute(
            delete(StudentTestScore).where(StudentTestScore.profile_id == profile.id)
        )
        await db_session.execute(
            delete(Experience).where(Experience.profile_id == profile.id)
        )
        # Same for the preference lists: earlier suites may have chosen their
        # own countries, and the persona only fills what is still empty.
        prefs = (
            (
                await db_session.execute(
                    select(ProfilePreference).where(ProfilePreference.profile_id == profile.id)
                )
            )
            .scalars()
            .first()
        )
        if prefs is not None:
            prefs.preferred_countries = []
            prefs.preferred_fields = []
            prefs.target_intakes = []
        await db_session.commit()

    await run_seed()
    db_session.expire_all()

    user = await _demo_user(db_session)
    assert user.role == UserRole.STUDENT, "the shared demo account must never be ADMIN"
    profile = await _demo_profile(db_session, user.id)

    # MASTER_SPEC §18 persona, filled because the fields were empty.
    assert profile.current_degree == "B.Tech Computer Science"
    assert profile.cgpa == Decimal("8.1")
    assert profile.cgpa_scale == Decimal("10")
    assert profile.budget_currency == "INR"
    assert profile.total_budget_amount == Decimal("1800000")
    assert profile.career_goal == "Machine learning engineer"
    assert profile.total_experience_months == 12

    # Persona rows are ensured, not duplicated.
    scores = (
        (
            await db_session.execute(
                select(StudentTestScore).where(StudentTestScore.profile_id == profile.id)
            )
        )
        .scalars()
        .all()
    )
    assert any(s.test_type == "IELTS" and s.overall_score == Decimal("7.5") for s in scores)
    experiences = (
        (
            await db_session.execute(
                select(Experience).where(Experience.profile_id == profile.id)
            )
        )
        .scalars()
        .all()
    )
    assert [e.title for e in experiences] == ["Software Engineering Intern"]

    prefs = (
        (
            await db_session.execute(
                select(ProfilePreference).where(ProfilePreference.profile_id == profile.id)
            )
        )
        .scalars()
        .one()
    )
    assert prefs.preferred_countries == ["DE"]
    assert prefs.target_intakes == ["Winter 2027"]

    # One clearly-labeled synthetic evidence fixture — never a real claim.
    assert await _count(db_session, Source, Source.canonical_url == DEMO_SOURCE_URL) == 1
    assert await _count(db_session, Evidence, Evidence.claim.like(f"{DEMO_LABEL}%")) == 1


async def test_seed_demo_is_idempotent(db_session: Any) -> None:
    await run_seed()
    db_session.expire_all()
    first = await _row_totals(db_session)

    await run_seed()
    db_session.expire_all()
    second = await _row_totals(db_session)

    assert second == first, f"a re-run duplicated rows: {first} -> {second}"
    assert first["labeled_evidence"] == 1
    assert first["sources"] == 1
    assert first["profiles"] == 1


async def test_seed_demo_demotes_a_promoted_demo_account(db_session: Any) -> None:
    """Regression: an ADMIN demo row must be corrected on the next seed run."""
    await run_seed()
    db_session.expire_all()
    user = await _demo_user(db_session)
    user.role = UserRole.ADMIN
    await db_session.commit()

    await run_seed()
    db_session.expire_all()

    refreshed = await _demo_user(db_session)
    assert refreshed.role == UserRole.STUDENT
