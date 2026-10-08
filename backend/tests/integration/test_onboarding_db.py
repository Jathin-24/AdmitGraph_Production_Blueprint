"""Onboarding answer mapping against real PostgreSQL (fixture: tests/conftest.py).

Form inputs arrive as strings; these tests lock in the coercion rules that
keep string answers out of typed columns (a raw '2027' in SMALLINT was a 500).
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import select

from app.core.errors import AppError
from app.db.models import EducationRecord, EducationSubject, ProfilePreference

# Aliased: pytest must not try to collect the SQLAlchemy model `TestScore` as a
# test class ("cannot collect test class 'TestScore' because it has a __init__").
from app.db.models import TestScore as StudentTestScore
from app.services.onboarding import answered_from_db, apply_answers
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


# --------------------------------------- shared-profile state snapshot/restore
#
# The suite runs against ONE demo profile, so every test that touches
# TestScore / education rows must leave the tables exactly as it found them —
# later tests (persist, simulator, strategy) read those rows.


async def _snapshot_state(session: Any, profile: Any) -> dict[str, Any]:
    scores = (
        (
            await session.execute(
                select(StudentTestScore).where(StudentTestScore.profile_id == profile.id)
            )
        )
        .scalars()
        .all()
    )
    records = (
        (
            await session.execute(
                select(EducationRecord).where(EducationRecord.profile_id == profile.id)
            )
        )
        .scalars()
        .all()
    )
    subjects: list[tuple[Any, ...]] = []
    for record in records:
        rows = (
            (
                await session.execute(
                    select(EducationSubject).where(
                        EducationSubject.education_record_id == record.id
                    )
                )
            )
            .scalars()
            .all()
        )
        subjects.extend(
            (s.education_record_id, s.subject_name, s.credits, s.grade_value, s.normalized_subject)
            for s in rows
        )
    return {
        "profile_education": (
            profile.current_degree,
            profile.institution_name,
            profile.field_of_study,
        ),
        "scores": [
            (s.test_type, s.overall_score, s.test_date, s.expiry_date, s.status, s.evidence_id)
            for s in scores
        ],
        "records": [
            (
                r.id,
                r.institution_name,
                r.country_code,
                r.degree,
                r.field_of_study,
                r.start_date,
                r.end_date,
                r.grade_value,
                r.grade_scale,
                r.grade_type,
                r.is_current,
            )
            for r in records
        ],
        "subjects": subjects,
    }


async def _restore_state(session: Any, profile: Any, state: dict[str, Any]) -> None:
    # Drop pending session state from the test's own work: an object whose
    # row was already deleted (flushed) must never be deleted a second time.
    session.expunge_all()
    profile.current_degree, profile.institution_name, profile.field_of_study = state[
        "profile_education"
    ]
    session.add(profile)

    records = (
        (
            await session.execute(
                select(EducationRecord).where(EducationRecord.profile_id == profile.id)
            )
        )
        .scalars()
        .all()
    )
    # Subjects first (flushed on their own), then their records — never two
    # dependent DELETEs in one flush where the record's cascade could win.
    for record in records:
        for subject in (
            (
                await session.execute(
                    select(EducationSubject).where(
                        EducationSubject.education_record_id == record.id
                    )
                )
            )
            .scalars()
            .all()
        ):
            await session.delete(subject)
    await session.flush()
    for record in records:
        await session.delete(record)
    for row in (
        (
            await session.execute(
                select(StudentTestScore).where(StudentTestScore.profile_id == profile.id)
            )
        )
        .scalars()
        .all()
    ):
        await session.delete(row)
    # DELETEs flush before the same-PK INSERTs restore the snapshot.
    await session.flush()
    for values in state["records"]:
        session.add(
            EducationRecord(
                id=values[0],
                institution_name=values[1],
                country_code=values[2],
                degree=values[3],
                field_of_study=values[4],
                start_date=values[5],
                end_date=values[6],
                grade_value=values[7],
                grade_scale=values[8],
                grade_type=values[9],
                is_current=values[10],
            )
        )
    await session.flush()
    for record_id, name, credits, grade_value, normalized in state["subjects"]:
        session.add(
            EducationSubject(
                education_record_id=record_id,
                subject_name=name,
                credits=credits,
                grade_value=grade_value,
                normalized_subject=normalized,
            )
        )
    for test_type, overall_score, test_date, expiry_date, status, evidence_id in state["scores"]:
        session.add(
            StudentTestScore(
                profile_id=profile.id,
                test_type=test_type,
                overall_score=overall_score,
                test_date=test_date,
                expiry_date=expiry_date,
                status=status,
                evidence_id=evidence_id,
            )
        )
    await session.commit()


async def _wipe_owned_rows(session: Any, profile: Any) -> None:
    """Deterministic start: the test owns the profile's score/education rows."""
    for row in (
        (
            await session.execute(
                select(StudentTestScore).where(StudentTestScore.profile_id == profile.id)
            )
        )
        .scalars()
        .all()
    ):
        await session.delete(row)
    for record in (
        (
            await session.execute(
                select(EducationRecord).where(EducationRecord.profile_id == profile.id)
            )
        )
        .scalars()
        .all()
    ):
        await session.delete(record)  # education_subjects cascade with it
    await session.commit()


async def test_real_english_test_type_dates_and_subjects_persist(
    db_session: Any,
) -> None:
    profile = await get_or_create_profile(db_session)
    state = await _snapshot_state(db_session, profile)
    try:
        await _wipe_owned_rows(db_session, profile)
        # A legacy save first: the real type must ADOPT this row instead of
        # duplicating the score (upgrade path for old payloads).
        await apply_answers(db_session, {"english_test_overall": "6.0"})

        answers = {
            "current_degree": "B.Tech Computer Science",
            "institution_name": "IIT Bombay",
            "english_test_overall": "7.0",
            "english_test_type": " ielts ",  # stored with canonical casing
            "english_test_date": "2025-06-01",
            "english_expiry_date": "2027-06-01",
            "subjects": [
                {"name": "Mathematics", "credits": "4"},
                {"name": "Physics", "credits": "3.5"},
                "Design",  # legacy entry: credits stay UNKNOWN, not zero
            ],
        }
        filled = await apply_answers(db_session, answers)
        assert filled == set(answers)

        scores = (
            (
                await db_session.execute(
                    select(StudentTestScore).where(StudentTestScore.profile_id == profile.id)
                )
            )
            .scalars()
            .all()
        )
        assert len(scores) == 1, "the legacy row must be adopted, never duplicated"
        score = scores[0]
        assert score.test_type == "IELTS"
        assert score.overall_score == Decimal("7.0")
        assert score.test_date == date(2025, 6, 1)
        assert score.expiry_date == date(2027, 6, 1)

        record = (
            (
                await db_session.execute(
                    select(EducationRecord).where(EducationRecord.profile_id == profile.id)
                )
            )
            .scalars()
            .one()
        )
        assert record.degree == "B.Tech Computer Science"
        assert record.institution_name == "IIT Bombay"
        assert record.is_current is True
        subjects = (
            (
                await db_session.execute(
                    select(EducationSubject).where(
                        EducationSubject.education_record_id == record.id
                    )
                )
            )
            .scalars()
            .all()
        )
        by_name = {s.subject_name: s for s in subjects}
        assert set(by_name) == {"Mathematics", "Physics", "Design"}
        assert by_name["Mathematics"].credits == Decimal("4")
        assert by_name["Physics"].credits == Decimal("3.5")
        assert by_name["Design"].credits is None
        assert by_name["Mathematics"].normalized_subject == "mathematics"

        # Re-submitting subjects replaces the rows instead of duplicating them.
        await apply_answers(db_session, {"subjects": [{"name": "Chemistry", "credits": "6"}]})
        replaced = (
            (
                await db_session.execute(
                    select(EducationSubject).where(
                        EducationSubject.education_record_id == record.id
                    )
                )
            )
            .scalars()
            .all()
        )
        assert [s.subject_name for s in replaced] == ["Chemistry"]
        assert replaced[0].credits == Decimal("6")
    finally:
        await _restore_state(db_session, profile, state)


async def test_legacy_payload_still_writes_only_the_english_overall_row(
    db_session: Any,
) -> None:
    profile = await get_or_create_profile(db_session)
    state = await _snapshot_state(db_session, profile)
    try:
        await _wipe_owned_rows(db_session, profile)
        filled = await apply_answers(db_session, {"english_test_overall": "6.5"})
        assert filled == {"english_test_overall"}

        scores = (
            (
                await db_session.execute(
                    select(StudentTestScore).where(StudentTestScore.profile_id == profile.id)
                )
            )
            .scalars()
            .all()
        )
        assert len(scores) == 1
        assert scores[0].test_type == "english_overall"
        assert scores[0].overall_score == Decimal("6.5")
        assert scores[0].test_date is None
        assert scores[0].expiry_date is None

        # Legacy payloads carry no subjects: education tables stay untouched,
        # so prerequisite matching keeps its valid UNKNOWN state.
        records = (
            (
                await db_session.execute(
                    select(EducationRecord).where(EducationRecord.profile_id == profile.id)
                )
            )
            .scalars()
            .all()
        )
        assert records == []
        assert "english_test_overall" in await answered_from_db(db_session)
    finally:
        await _restore_state(db_session, profile, state)


async def test_progress_credits_scored_rows_but_never_scoreless_ones(
    db_session: Any,
) -> None:
    profile = await get_or_create_profile(db_session)
    state = await _snapshot_state(db_session, profile)
    try:
        await _wipe_owned_rows(db_session, profile)
        # Type + date without a score: recorded honestly, credits nothing.
        filled = await apply_answers(
            db_session, {"english_test_type": "TOEFL", "english_test_date": "2026-01-15"}
        )
        assert filled == {"english_test_type", "english_test_date"}
        assert "english_test_overall" not in await answered_from_db(db_session)

        # Adding the score to the same test credits the question, no dup row,
        # and the earlier date survives a payload that omits the date keys.
        await apply_answers(
            db_session, {"english_test_overall": "96", "english_test_type": "TOEFL"}
        )
        assert "english_test_overall" in await answered_from_db(db_session)
        scores = (
            (
                await db_session.execute(
                    select(StudentTestScore).where(StudentTestScore.profile_id == profile.id)
                )
            )
            .scalars()
            .all()
        )
        assert len(scores) == 1
        assert scores[0].test_type == "TOEFL"
        assert scores[0].overall_score == Decimal("96")
        assert scores[0].test_date == date(2026, 1, 15)
    finally:
        await _restore_state(db_session, profile, state)
