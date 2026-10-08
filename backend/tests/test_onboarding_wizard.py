"""Seven-step wizard coverage: schema, coercion, and progress semantics.

Runs against real PostgreSQL (fixture: tests/conftest.py). The rules locked in
here are the ones the frontend wizard and GET /onboarding/progress depend on:
every question has a storage home, form strings are coerced (never shoved into
typed columns), blanks clear, bad numbers 400, and REQUIRED_KEYS still means
exactly the original four required answers.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.api.v1.onboarding import progress
from app.core.errors import AppError
from app.db.models import ProfilePreference, Skill, StudentProfile
from app.services import onboarding as onboarding_service
from app.services.onboarding import apply_answers
from app.services.profile import get_or_create_profile, validate_profile


def test_schema_is_seven_steps_with_original_required_keys() -> None:
    schema = onboarding_service.ONBOARDING_SCHEMA
    assert [step.id for step in schema.steps] == [
        "goal",
        "education",
        "tests",
        "experience",
        "budget",
        "preferences",
        "review",
    ]
    # Progress math and the coordinator's regression tests pin these four.
    assert onboarding_service.REQUIRED_KEYS == {
        "field_of_study",
        "current_degree",
        "cgpa",
        "total_budget_amount",
    }
    keys = [f.key for step in schema.steps for f in step.fields]
    assert len(keys) == len(set(keys)), "answer keys must be unique across steps"
    for step in schema.steps:
        if step.id == "review":
            assert step.fields == [], "review is a summary, not a new question"
            continue
        assert step.fields, f"step {step.id} needs at least one question"
        for field in step.fields:
            assert field.why_we_ask.strip(), f"{field.key} is missing why-we-ask copy"
            assert field.explanation.strip(), f"{field.key} is missing an explanation"


def test_every_schema_key_has_a_storage_home() -> None:
    """No question may be silently dropped by apply_answers."""
    schema_keys = {
        f.key for step in onboarding_service.ONBOARDING_SCHEMA.steps for f in step.fields
    }
    stored = (
        onboarding_service.PROFILE_FIELD_MAP
        | onboarding_service.PREFERENCE_FIELD_MAP
        | onboarding_service.PREFERENCE_LIST_MAP
        | onboarding_service.PREFERENCE_TEXT_MAP
        | onboarding_service.PREFERENCE_DECIMAL_MAP
        | onboarding_service.SKILL_FIELD_MAP
        | onboarding_service.TEST_SCORE_FIELDS
    )
    assert schema_keys - stored == set(), "questions without a column to land in"
    assert stored - schema_keys == set(), "mapped keys without a question"
    # The English-test companions and `subjects` are payload-only: answers the
    # form may send alongside a question, not new wizard questions — so the
    # schema (and REQUIRED_KEYS) deliberately stay untouched.
    payload_only = set(onboarding_service.ENGLISH_TEST_META_FIELDS) | set(
        onboarding_service.SUBJECT_FIELDS
    )
    assert not (schema_keys & payload_only)


def test_server_defaults_are_not_treated_as_answers() -> None:
    """backlogs/0, experience/0 and scholarship/false are defaults, not answers."""
    assert onboarding_service._is_answered("total_experience_months", 0) is False
    assert onboarding_service._is_answered("total_experience_months", 6) is True
    assert onboarding_service._is_answered("backlogs", 0) is False
    assert onboarding_service._is_answered("scholarship_dependence", False) is False
    assert onboarding_service._is_answered("scholarship_dependence", True) is True
    assert onboarding_service._is_answered("cgpa", None) is False
    assert onboarding_service._is_answered("cgpa", "8.1") is True


async def test_new_step_answers_coerce_and_roundtrip(db_session: Any) -> None:
    answers = {
        "total_experience_months": "24",
        "skills": "Python, SQL",
        "cgpa_scale": "10",
        "percentage": "78.5",
        "backlogs": "2",
        "annual_budget_amount": "300000",
        "scholarship_dependence": "Yes",
        "preferred_degree_types": "Master's",
        "preferred_cities": "Munich, Berlin",
        "excluded_countries": "France",
        "career_market_importance": "7.5",
        "research_preference": "Coursework",
    }
    filled = await apply_answers(db_session, answers)
    assert filled == set(answers)

    profile = await get_or_create_profile(db_session)
    assert profile.total_experience_months == 24
    assert isinstance(profile.total_experience_months, int)
    assert profile.cgpa_scale == Decimal("10")
    assert profile.percentage == Decimal("78.5")
    assert profile.backlogs == 2
    assert profile.annual_budget_amount == Decimal("300000")
    assert profile.scholarship_dependence is True
    # Preference questions have no StudentProfile column — they must not leak.
    assert not hasattr(profile, "preferred_degree_types")
    assert not hasattr(profile, "career_market_importance")

    prefs = (
        (
            await db_session.execute(
                select(ProfilePreference).where(ProfilePreference.profile_id == profile.id)
            )
        )
        .scalars()
        .one()
    )
    assert prefs.preferred_degree_types == ["Master's"]
    assert prefs.preferred_cities == ["Munich", "Berlin"]
    assert prefs.excluded_countries == ["France"]
    assert prefs.career_market_importance == Decimal("7.5")
    assert prefs.research_preference == "Coursework"

    skills = (
        (
            await db_session.execute(select(Skill).where(Skill.profile_id == profile.id))
        )
        .scalars()
        .all()
    )
    assert sorted(s.skill_name for s in skills) == ["Python", "SQL"]
    assert all(s.source == "onboarding" for s in skills)


async def test_blank_clears_new_fields_and_bad_numbers_400(db_session: Any) -> None:
    await apply_answers(
        db_session,
        {
            "total_experience_months": "10",
            "skills": "Python",
            "career_market_importance": "6",
            "research_preference": "Mixed",
            "excluded_countries": "France",
            "scholarship_dependence": "No",
            "percentage": "70",
        },
    )
    cleared = await apply_answers(
        db_session,
        {
            "total_experience_months": "",
            "skills": "   ",
            "career_market_importance": "",
            "research_preference": "   ",
            "excluded_countries": "",
            "scholarship_dependence": "",
            "percentage": "",
        },
    )
    assert cleared == {
        "total_experience_months",
        "skills",
        "career_market_importance",
        "research_preference",
        "excluded_countries",
        "scholarship_dependence",
        "percentage",
    }

    profile = await get_or_create_profile(db_session)
    assert profile.total_experience_months is None
    assert profile.scholarship_dependence is None
    assert profile.percentage is None
    prefs = (
        (
            await db_session.execute(
                select(ProfilePreference).where(ProfilePreference.profile_id == profile.id)
            )
        )
        .scalars()
        .one()
    )
    assert prefs.career_market_importance is None
    assert prefs.research_preference is None
    assert prefs.excluded_countries == []
    skills = (
        (
            await db_session.execute(
                select(Skill).where(
                    Skill.profile_id == profile.id, Skill.source == "onboarding"
                )
            )
        )
        .scalars()
        .all()
    )
    assert skills == []

    for bad in (
        {"career_market_importance": "high"},
        {"total_experience_months": "1.5"},
        {"backlogs": "two"},
        {"scholarship_dependence": "maybe"},
        {"percentage": "8.1.5"},
    ):
        with pytest.raises(AppError) as exc:
            await apply_answers(db_session, bad)
        assert exc.value.status_code == 400


async def test_preferences_roundtrip_through_real_columns(db_session: Any) -> None:
    answers = {
        "preferred_degree_types": ["Master's", "PhD"],
        "target_intakes": "Winter 2027, Summer 2027",
        "preferred_cities": "Munich",
        "excluded_countries": ["France"],
        "career_market_importance": "9.25",
        "research_preference": "Research-heavy",
    }
    filled = await apply_answers(db_session, answers)
    assert filled == set(answers)

    profile = await get_or_create_profile(db_session)
    prefs = (
        (
            await db_session.execute(
                select(ProfilePreference).where(ProfilePreference.profile_id == profile.id)
            )
        )
        .scalars()
        .one()
    )
    assert prefs.preferred_degree_types == ["Master's", "PhD"]
    assert prefs.target_intakes == ["Winter 2027", "Summer 2027"]
    assert prefs.preferred_cities == ["Munich"]
    assert prefs.excluded_countries == ["France"]
    assert prefs.career_market_importance == Decimal("9.25")
    assert prefs.research_preference == "Research-heavy"
    assert not hasattr(profile, "preferred_cities")


async def test_progress_keeps_original_required_key_semantics(db_session: Any) -> None:
    # Optional questions never show up as "still needed".
    await apply_answers(
        db_session,
        {"career_market_importance": "", "total_experience_months": "", "skills": ""},
    )
    early = await progress(db_session)
    assert set(early.missing_required_keys) <= onboarding_service.REQUIRED_KEYS
    assert "career_market_importance" not in early.missing_required_keys
    assert "career_market_importance" not in early.answered_keys

    # Answering only the original required keys completes the requirement set.
    required = {
        "field_of_study": "Computer Science",
        "current_degree": "B.Tech Computer Science",
        "cgpa": "8.10",
        "total_budget_amount": "1800000",
    }
    await apply_answers(db_session, required)
    mid = await progress(db_session)
    assert mid.missing_required_keys == []
    assert set(required) <= set(mid.answered_keys)
    assert mid.completion_percent == 100.0

    # New recommended keys are tracked as answered and keep the total at 100%.
    await apply_answers(
        db_session, {"total_experience_months": "12", "skills": "Python", "career_market_importance": "8"}
    )
    late = await progress(db_session)
    assert {"total_experience_months", "skills", "career_market_importance"} <= set(
        late.answered_keys
    )
    assert late.missing_required_keys == []
    assert late.completion_percent == 100.0
    assert late.completion_percent >= mid.completion_percent


# ------------------------------------------- English test companions (payload-only)


def test_english_test_type_defaults_canonicalizes_and_preserves_reported() -> None:
    pick = onboarding_service._english_test_type
    # Legacy payloads (no companion key) keep mapping to english_overall.
    assert pick({}) == "english_overall"
    assert pick({"english_test_type": "   "}) == "english_overall"
    # Known tests are stored with canonical casing so exact-match readers work.
    assert pick({"english_test_type": " ielts "}) == "IELTS"
    assert pick({"english_test_type": "toefl"}) == "TOEFL"
    # Anything else is stored exactly as reported — never guessed.
    assert pick({"english_test_type": "Duolingo English Test"}) == "Duolingo English Test"


def test_english_date_coercion_accepts_iso_and_400s_garbage() -> None:
    coerce = onboarding_service._to_date
    assert coerce("english_test_date", "2027-06-30") == date(2027, 6, 30)
    assert coerce("english_expiry_date", "") is None
    assert coerce("english_expiry_date", None) is None
    for bad in ("next june", "2027-13-40", "30-06-2027"):
        with pytest.raises(AppError) as exc:
            coerce("english_test_date", bad)
        assert exc.value.status_code == 400


def test_subjects_answer_accepts_legacy_and_object_forms() -> None:
    parse = onboarding_service._to_subjects
    # Legacy: a plain list of names (or comma text), credits stay UNKNOWN.
    assert parse(["Mathematics", " Physics "]) == [("Mathematics", None), ("Physics", None)]
    assert parse("Math, Physics") == [("Math", None), ("Physics", None)]
    # New: {name, credits} objects; missing credits stay UNKNOWN, not zero.
    assert parse([{"name": "Mathematics", "credits": "4"}, {"name": "Physics"}]) == [
        ("Mathematics", Decimal("4")),
        ("Physics", None),
    ]
    # Blanks drop and duplicates collapse case-insensitively.
    assert parse(["Math", "math", "   "]) == [("Math", None)]


def test_subjects_answer_rejects_malformed_entries() -> None:
    parse = onboarding_service._to_subjects
    for bad in (
        [{"credits": "4"}],  # object without a name
        [{"name": "Math", "credits": "half"}],  # non-numeric credits
        [{"name": "Math", "credits": "-2"}],  # negative credits
    ):
        with pytest.raises(AppError) as exc:
            parse(bad)
        assert exc.value.status_code == 400


# ------------------------------------------------- profile validation unchanged


def test_profile_validation_still_requires_cgpa_or_percentage() -> None:
    empty = StudentProfile(user_id=uuid4())
    out = validate_profile(empty)
    assert out.valid is False
    assert any("Provide either CGPA or percentage" in issue.message for issue in out.issues)

    percentage_only = StudentProfile(user_id=uuid4(), percentage=Decimal("78.5"))
    assert validate_profile(percentage_only).valid is True

    cgpa_only = StudentProfile(user_id=uuid4(), cgpa=Decimal("8.1"))
    assert validate_profile(cgpa_only).valid is True
