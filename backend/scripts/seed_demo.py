"""Seed a safe synthetic demo student and clearly-labeled demo evidence fixtures.

Run: python -m scripts.seed_demo   (database must be migrated)
"""

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app.db.models import (
    ConfidenceLevel,
    Evidence,
    ProfilePreference,
    Source,
    StudentProfile,
    User,
)
from app.db.session import get_engine
from sqlalchemy.ext.asyncio import async_sessionmaker

DEMO_LABEL = "DEMO FIXTURE - synthetic, not a real university claim"


async def main() -> None:
    get_engine()
    maker = async_sessionmaker(get_engine(), expire_on_commit=False)
    async with maker() as session:
        existing = await session.execute(select(User).where(User.email == "demo@admitgraph.local"))
        if existing.scalar_one_or_none() is not None:
            print("Demo user already seeded.")
            return
        user = User(email="demo@admitgraph.local", full_name="Demo Student (synthetic)")
        session.add(user)
        await session.flush()
        profile = StudentProfile(
            user_id=user.id,
            current_degree="B.Tech Computer Science",
            field_of_study="Computer Science",
            institution_name="Synthetic Institute of Technology",
            institution_country_code="IN",
            graduation_year=2027,
            cgpa=8.1,
            cgpa_scale=10,
            total_experience_months=6,
            budget_currency="INR",
            total_budget_amount=1800000,
            career_goal="Machine learning engineer",
        )
        session.add(profile)
        await session.flush()
        session.add(
            ProfilePreference(
                profile_id=profile.id,
                preferred_countries=["DE"],
                preferred_fields=["Artificial Intelligence", "Computer Science"],
                target_intakes=["Winter 2027"],
            )
        )
        source = Source(
            url="https://example.edu/synthetic-msc-ai",
            canonical_url="https://example.edu/synthetic-msc-ai",
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
                subject_id=uuid.uuid4(),
                claim=DEMO_LABEL + ": demo program lists IELTS 6.5 requirement.",
                normalized_claim="ielts_overall_min",
                extracted_value={"min": 6.5, "test": "IELTS", "demo": True},
                confidence=ConfidenceLevel.LOW,
                retrieved_at=datetime.now(UTC),
                freshness_deadline=datetime.now(UTC) + timedelta(days=30),
                extraction_version="demo-fixture-v1",
            )
        )
        await session.commit()
        print("Seeded demo student + demo evidence fixtures.")


if __name__ == "__main__":
    asyncio.run(main())
