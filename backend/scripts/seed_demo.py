"""Seed a safe synthetic demo student and clearly-labeled demo evidence fixtures.

Idempotent: safe to re-run — it fills only what is missing, never overwrites
existing values, and duplicates nothing. Covers the MASTER_SPEC §18 persona
(B.Tech CSE, CGPA 8.1/10, IELTS 7.5, one 12-month internship, ₹18,00,000 INR
budget, Germany, MSc AI, Winter 2027), one clearly-labeled demo evidence
fixture, and a demo user seeded STUDENT (P0-2: a shared admin-capable demo
account is how anonymous callers used to reach /admin/*).

Local admin exploration is opt-in via `python -m scripts.grant_demo_admin`.

Run: python -m scripts.seed_demo   (database must be migrated)
"""

import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.db.models import (
    ConfidenceLevel,
    Evidence,
    ProfilePreference,
    Source,
    StudentProfile,
    User,
    UserRole,
)
from app.db.session import get_engine
from app.services.demo.persona import PERSONA_LABEL, apply_demo_persona

DEMO_LABEL = "DEMO FIXTURE - synthetic, not a real university claim"
DEMO_EMAIL = "demo@admitgraph.local"

# Existing (already seeded) demo students keep their values; fresh rows get the
# full MASTER_SPEC §18 persona via apply_demo_persona below.
FRESH_PROFILE_VALUES = {
    "current_degree": "B.Tech Computer Science",
    "field_of_study": "Computer Science",
    "institution_name": "Synthetic Institute of Technology",
    "institution_country_code": "IN",
    "graduation_year": 2027,
    "cgpa": Decimal("8.1"),
    "cgpa_scale": Decimal("10"),
    "total_experience_months": 12,
    "budget_currency": "INR",
    "total_budget_amount": Decimal("1800000"),
    "career_goal": "Machine learning engineer",
}


async def main() -> None:
    get_engine()
    maker = async_sessionmaker(get_engine(), expire_on_commit=False)
    async with maker() as session:
        result = await session.execute(select(User).where(User.email == DEMO_EMAIL))
        user = result.scalar_one_or_none()
        fresh_user = user is None
        if user is None:
            user = User(email=DEMO_EMAIL, full_name="Demo Student (synthetic)")
            session.add(user)
            await session.flush()

        # P0-2: the shared demo account is STUDENT, asserted every run (the
        # old seeding promoted it to ADMIN, so anonymous traffic inherited an
        # admin-capable row). Grant admin locally on purpose instead:
        # python -m scripts.grant_demo_admin.
        user.role = UserRole.STUDENT

        result = await session.execute(
            select(StudentProfile).where(StudentProfile.user_id == user.id)
        )
        profile = result.scalar_one_or_none()
        if profile is None:
            profile = StudentProfile(user_id=user.id, **FRESH_PROFILE_VALUES)
            session.add(profile)
            await session.flush()
        if fresh_user:
            session.add(
                ProfilePreference(
                    profile_id=profile.id,
                    preferred_countries=["DE"],
                    preferred_fields=["Artificial Intelligence", "Computer Science"],
                    target_intakes=["Winter 2027"],
                )
            )
            await session.flush()

        # MASTER_SPEC §18 persona: fills only empty fields, adds the IELTS 7.5
        # score rows and the labeled 12-month internship when they are missing.
        await apply_demo_persona(session, profile)

        # One clearly-labeled demo evidence fixture (created at most once).
        source_url = "https://example.edu/synthetic-msc-ai"
        result = await session.execute(select(Source).where(Source.canonical_url == source_url))
        if result.scalar_one_or_none() is None:
            source = Source(
                url=source_url,
                canonical_url=source_url,
                domain="example.edu",
                title=DEMO_LABEL,
                source_type="demo_fixture",
                last_seen_at=datetime.now(UTC),
            )
            session.add(source)
            await session.flush()
            session.add(
                Evidence(
                    source_id=source.id,
                    search_result_id=None,
                    claim_type="language",
                    subject_type="program",
                    subject_id=None,
                    claim=DEMO_LABEL + ": demo program lists IELTS 6.5 requirement.",
                    normalized_claim="ielts_overall_min",
                    extracted_value={"min": 6.5, "test": "IELTS", "demo": True},
                    confidence=ConfidenceLevel.LOW,
                    retrieved_at=datetime.now(UTC),
                    freshness_deadline=datetime.now(UTC) + timedelta(days=30),
                    extraction_version="demo-fixture-v1",
                )
            )
            print("Seeded labeled demo evidence fixture.")
        else:
            print("Demo evidence fixture already present.")

        await session.commit()
        print(
            f"Demo user {'created' if fresh_user else 'updated'} "
            f"({PERSONA_LABEL}); role=STUDENT, persona + scores + internship ensured."
        )


if __name__ == "__main__":
    asyncio.run(main())
