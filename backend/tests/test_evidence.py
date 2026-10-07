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
