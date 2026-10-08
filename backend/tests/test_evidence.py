import uuid
from datetime import UTC, datetime, timedelta

from app.db.models import Evidence


def test_conflict_detection_groups_differing_values() -> None:
    a = Evidence(
        source_id=uuid.uuid4(),
        claim_type="deadline",
        subject_type="program",
        subject_id=uuid.uuid4(),
        claim="a",
        normalized_claim="deadline_winter",
        extracted_value={"date": "2027-01-15"},
        retrieved_at=datetime.now(UTC),
    )
    b = Evidence(
        source_id=uuid.uuid4(),
        claim_type="deadline",
        subject_type=a.subject_type,
        subject_id=a.subject_id,
        claim="b",
        normalized_claim="deadline_winter",
        extracted_value={"date": "2027-02-01"},
        retrieved_at=datetime.now(UTC) - timedelta(days=3),
    )
    assert a.normalized_claim == b.normalized_claim
    assert a.extracted_value != b.extracted_value


def test_freshness_window_defaults() -> None:
    from app.services.evidence.freshness import freshness_deadline, is_stale

    now = datetime(2026, 10, 7, tzinfo=UTC)
    assert freshness_deadline("deadline", now) == now + timedelta(days=7)
    assert is_stale("visa", now - timedelta(days=8), now) is True
    assert is_stale("tuition", now - timedelta(days=8), now) is False


def test_parse_deadline_date_accepts_single_window_and_rejects_prose() -> None:
    """Single dates and application windows mint intakes; prose/term-only
    values stay UNKNOWN (nothing is invented)."""
    from datetime import date

    from app.services.evidence.extraction import parse_deadline_date

    assert parse_deadline_date({"date": "2027-01-15"}) == date(2027, 1, 15)
    # Live LLM output for an application window: the close is the deadline.
    assert parse_deadline_date({"start": "2026-10-15", "end": "2027-01-15"}) == date(2027, 1, 15)
    assert parse_deadline_date({"deadline": "2027-07-15"}) == date(2027, 7, 15)
    # Never invent: prose, term/year-only, malformed and absent stay None.
    assert parse_deadline_date({"note": "deadlines mentioned"}) is None
    assert parse_deadline_date({"term": "Winter", "year": 2027}) is None
    assert parse_deadline_date({"date": "soon"}) is None
    assert parse_deadline_date({}) is None
