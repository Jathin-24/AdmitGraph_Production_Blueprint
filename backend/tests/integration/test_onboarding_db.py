"""Onboarding answer mapping against real PostgreSQL (fixture: tests/conftest.py).

Form inputs arrive as strings; these tests lock in the coercion rules that
keep string answers out of typed columns (a raw '2027' in SMALLINT was a 500).
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import select

from app.core.errors import AppError
from app.db.models import ProfilePreference

# Aliased: pytest must not try to collect the SQLAlchemy model `TestScore` as a
# test class ("cannot collect test class 'TestScore' because it has a __init__").
from app.db.models import TestScore as StudentTestScore
from app.services.onboarding import apply_answers
from app.services.profile import get_or_create_profile


async def test_apply_answers_coerces_form_strings_to_column_types(
    db_session: Any,
) -> None:
    answers = {
        "graduation_year": "2027",
        "cgpa": "8.10",
        "total_budget_amount": "1800000",
        "current_degree": "B.Tech Computer Science",
        "institution_name": "IIT Bombay",
        "career_goal": "   ",  # cleared field must become NULL, not blank text
        "preferred_countries": "Germany, Netherlands",
        "target_intakes": ["Winter 2027"],
        "english_test_overall": "7.5",
    }
    filled = await apply_answers(db_session, answers)
    assert filled == set(answers)

    profile = await get_or_create_profile(db_session)
    assert profile.graduation_year == 2027
    assert isinstance(profile.graduation_year, int)
    assert profile.cgpa == Decimal("8.10")
    assert profile.total_budget_amount == Decimal("1800000")
    assert profile.current_degree == "B.Tech Computer Science"
    assert profile.career_goal is None

    prefs = (
        await db_session.execute(
            select(ProfilePreference).where(ProfilePreference.profile_id == profile.id)
        )
    ).scalar_one()
    assert prefs.preferred_countries == ["Germany", "Netherlands"]
    assert prefs.target_intakes == ["Winter 2027"]

    score = (
        await db_session.execute(
            select(StudentTestScore).where(StudentTestScore.profile_id == profile.id)
        )
    ).scalars().first()
    assert score is not None
    assert score.test_type == "english_overall"
    assert score.overall_score == Decimal("7.5")

    # Re-saving the same answers upserts instead of duplicating the score.
    await apply_answers(db_session, {"english_test_overall": "7.0"})
    scores = (
        await db_session.execute(
            select(StudentTestScore).where(StudentTestScore.profile_id == profile.id)
        )
    ).scalars().all()
    assert len(scores) == 1
    assert scores[0].overall_score == Decimal("7.0")


async def test_apply_answers_rejects_invalid_numbers_with_400(
    db_session: Any,
) -> None:
    with pytest.raises(AppError) as exc:
        await apply_answers(db_session, {"graduation_year": "not-a-year"})
    assert exc.value.status_code == 400

    with pytest.raises(AppError) as exc:
        await apply_answers(db_session, {"cgpa": "8.1.5"})
    assert exc.value.status_code == 400
