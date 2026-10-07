"""Backend-driven onboarding schema and answer mapping."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError
from app.db.models import ProfilePreference, TestScore
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

# Form inputs arrive as strings; these keys target typed columns and must be
# converted before touching the database (a string in SMALLINT is a 500).
INT_FIELDS = {"graduation_year"}
DECIMAL_FIELDS = {"cgpa", "total_budget_amount"}
TEST_SCORE_FIELDS = {"english_test_overall"}
ENGLISH_TEST_TYPE = "english_overall"

REQUIRED_KEYS = {f.key for s in ONBOARDING_SCHEMA.steps for f in s.fields if f.required}


def _to_decimal(key: str, raw: Any) -> Decimal | None:
    """Coerce a form value to Decimal; blank/None clears the value."""
    if raw is None:
        return None
    if isinstance(raw, str):
        raw = raw.strip()
        if not raw:
            return None
    try:
        value = Decimal(str(raw))
    except InvalidOperation:
        raise AppError(400, "VALIDATION_ERROR", f"Invalid number for {key}") from None
    if not value.is_finite():
        raise AppError(400, "VALIDATION_ERROR", f"Invalid number for {key}") from None
    return value


def _to_int(key: str, raw: Any) -> int | None:
    value = _to_decimal(key, raw)
    if value is None:
        return None
    if value != value.to_integral_value():
        raise AppError(400, "VALIDATION_ERROR", f"Expected a whole number for {key}")
    return int(value)


def _to_list(raw: Any) -> list[str]:
    """Normalize a list answer: arrays pass through, comma text is split."""
    if isinstance(raw, list):
        items = [str(v).strip() for v in raw]
    else:
        items = [part.strip() for part in str(raw).split(",")]
    return [item for item in items if item]


async def _upsert_english_score(
    session: AsyncSession, profile_id: UUID, score: Decimal | None
) -> None:
    result = await session.execute(
        select(TestScore).where(
            TestScore.profile_id == profile_id,
            TestScore.test_type == ENGLISH_TEST_TYPE,
        )
    )
    row = result.scalar_one_or_none()
    if score is None:
        if row is not None:
            await session.delete(row)
    elif row is None:
        session.add(
            TestScore(profile_id=profile_id, test_type=ENGLISH_TEST_TYPE, overall_score=score)
        )
    else:
        row.overall_score = score


def answered_keys(profile_filled: set[str], preferences_filled: set[str]) -> list[str]:
    return sorted(profile_filled | preferences_filled)


async def apply_answers(session: AsyncSession, answers: dict[str, Any]) -> set[str]:
    """Persist onboarding answers with column-accurate types.

    Form values are strings: numeric fields are converted (or rejected with a
    400 instead of a 500), blank values clear the column, list answers are
    split on commas, and the English test score upserts a TestScore row.
    """
    profile = await get_or_create_profile(session)
    filled: set[str] = set()
    result = await session.execute(
        select(ProfilePreference).where(ProfilePreference.profile_id == profile.id)
    )
    preferences = result.scalar_one()
    for key, value in answers.items():
        if key in PROFILE_FIELD_MAP:
            if key in INT_FIELDS:
                setattr(profile, key, _to_int(key, value))
            elif key in DECIMAL_FIELDS:
                setattr(profile, key, _to_decimal(key, value))
            else:
                text = value.strip() if isinstance(value, str) else value
                setattr(profile, key, text or None)
            filled.add(key)
        elif key in PREFERENCE_FIELD_MAP:
            setattr(preferences, key, _to_list(value))
            filled.add(key)
        elif key in TEST_SCORE_FIELDS:
            await _upsert_english_score(session, profile.id, _to_decimal(key, value))
            filled.add(key)
    await session.commit()
    return filled
