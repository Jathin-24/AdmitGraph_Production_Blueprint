"""Backend-driven onboarding schema and answer mapping."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import ProfilePreference
from app.schemas.onboarding import OnboardingField, OnboardingSchema, OnboardingStep
from app.services.profile import get_or_create_profile

ONBOARDING_SCHEMA = OnboardingSchema(
    version="v1",
    steps=[
        OnboardingStep(
            id="goal",
            title="Goal",
            fields=[
                OnboardingField(
                    key="field_of_study",
                    question="What field do you want to study?",
                    explanation="This helps us find programs that match your interests.",
                    example="Computer Science, Artificial Intelligence",
                    why_we_ask="Programs are organized by field; guessing here wastes your time later.",
                    input_type="text",
                    required=True,
                ),
                OnboardingField(
                    key="career_goal",
                    question="What is your career goal?",
                    explanation="Where you want to end up shapes which programs are worth your money.",
                    example="ML engineer in Germany",
                    why_we_ask="Career-fit scoring compares program outcomes to this goal.",
                    input_type="text",
                ),
                OnboardingField(
                    key="preferred_countries",
                    question="Which countries interest you?",
                    explanation="You can change this later.",
                    example="Germany, Netherlands",
                    why_we_ask="Country choice drives visa, tuition, and deadline rules.",
                    input_type="list",
                ),
                OnboardingField(
                    key="target_intakes",
                    question="When do you want to start?",
                    explanation="An intake is the term a program admits new students.",
                    example="Winter 2027",
                    why_we_ask="Deadlines and offers depend on intake.",
                    input_type="list",
                ),
            ],
        ),
        OnboardingStep(
            id="education",
            title="Education",
            fields=[
                OnboardingField(
                    key="current_degree",
                    question="What is your current degree?",
                    explanation="Your background determines which programs you can enter.",
                    example="B.Tech Computer Science",
                    why_we_ask="Most master's programs require a related bachelor's degree.",
                    input_type="text",
                    required=True,
                ),
                OnboardingField(
                    key="institution_name",
                    question="Which institution?",
                    explanation="Programs may weigh your institution differently.",
                    example="IIT Bombay",
                    why_we_ask="Important for credential evaluation; mark UNKNOWN if unsure.",
                    input_type="text",
                ),
                OnboardingField(
                    key="cgpa",
                    question="Your CGPA?",
                    explanation="CGPA is your cumulative grade point average.",
                    example="8.1",
                    why_we_ask="Used to check academic minimums.",
                    input_type="number",
                    required=True,
                ),
                OnboardingField(
                    key="graduation_year",
                    question="Expected graduation year?",
                    explanation="Most intakes expect a recent or upcoming degree.",
                    example="2026",
                    why_we_ask="Determines which intake you can realistically target.",
                    input_type="number",
                ),
            ],
        ),
        OnboardingStep(
            id="tests",
            title="Tests",
            fields=[
                OnboardingField(
                    key="english_test_overall",
                    question="English test overall score?",
                    explanation=(
                        "IELTS/TOEFL/PTE prove language ability; scores usually expire after 2 years."
                    ),
                    example="IELTS 7.5",
                    why_we_ask=(
                        "Language fit cannot be computed without it. If you have none, "
                        "we flag a risk instead of guessing."
                    ),
                    input_type="number",
                ),
            ],
        ),
        OnboardingStep(
            id="budget",
            title="Budget",
            fields=[
                OnboardingField(
                    key="total_budget_amount",
                    question="Total budget for your studies?",
                    explanation="Includes tuition and living costs.",
                    example="1800000 INR",
                    why_we_ask="Financial fit prevents plans you cannot fund.",
                    input_type="number",
                    required=True,
                ),
            ],
        ),
    ],
)

PROFILE_FIELD_MAP = {
    "field_of_study",
    "career_goal",
    "current_degree",
    "institution_name",
    "cgpa",
    "graduation_year",
    "total_budget_amount",
}
PREFERENCE_FIELD_MAP = {"preferred_countries", "target_intakes"}

REQUIRED_KEYS = {f.key for s in ONBOARDING_SCHEMA.steps for f in s.fields if f.required}


def answered_keys(profile_filled: set[str], preferences_filled: set[str]) -> list[str]:
    return sorted(profile_filled | preferences_filled)


async def apply_answers(session: AsyncSession, answers: dict[str, Any]) -> set[str]:
    profile = await get_or_create_profile(session)
    filled: set[str] = set()
    result = await session.execute(
        select(ProfilePreference).where(ProfilePreference.profile_id == profile.id)
    )
    preferences = result.scalar_one()
    for key, value in answers.items():
        if key in PROFILE_FIELD_MAP:
            setattr(profile, key, value)
            filled.add(key)
        elif key in PREFERENCE_FIELD_MAP:
            setattr(preferences, key, value if isinstance(value, list) else [value])
            filled.add(key)
    await session.commit()
    return filled
