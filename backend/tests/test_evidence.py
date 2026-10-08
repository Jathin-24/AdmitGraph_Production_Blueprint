import uuid
from datetime import UTC, datetime, timedelta

from app.db.models import Evidence, SearchResult


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


def _stored_result(
    *,
    raw_payload: dict[str, object] | None = None,
    title: str | None = None,
    snippet: str | None = None,
) -> SearchResult:
    """In-memory SearchResult (never flushed) for publication-date parsing."""
    return SearchResult(
        search_run_id=uuid.uuid4(),
        raw_payload=dict(raw_payload or {}),
        title=title,
        snippet=snippet,
    )


def test_extract_published_at_reads_real_payload_dates() -> None:
    from app.services.evidence.extraction import extract_published_at

    # Explicit provider date field (ISO + unix epoch seconds).
    iso = _stored_result(raw_payload={"date": "2026-03-04"}, title="T", snippet="S")
    assert extract_published_at(iso) == datetime(2026, 3, 4, tzinfo=UTC)
    epoch = _stored_result(raw_payload={"published_at": 1767225600}, title="T", snippet="S")
    assert extract_published_at(epoch) == datetime(2026, 1, 1, tzinfo=UTC)

    # google_jobs nests the posting date under detected_extensions.
    jobs = _stored_result(
        raw_payload={"detected_extensions": {"posted_at": "2026-01-15T10:30:00Z"}},
        title="Job",
        snippet="x",
    )
    assert extract_published_at(jobs) == datetime(2026, 1, 15, 10, 30, tzinfo=UTC)


def test_extract_published_at_requires_a_genuine_date() -> None:
    from app.services.evidence.extraction import extract_published_at

    # Relative phrases are not points in time: UNKNOWN, never guessed.
    relative = _stored_result(raw_payload={"date": "30+ days ago"}, title="T", snippet="S")
    assert extract_published_at(relative) is None
    # A deadline in the snippet must never become a publication date.
    deadline = _stored_result(title="T", snippet="Application deadline is 2027-01-15.")
    assert extract_published_at(deadline) is None
    # A labelled date IS the publication date.
    labelled = _stored_result(title="T", snippet="Updated Jan 15, 2026 - admissions rules changed.")
    assert extract_published_at(labelled) == datetime(2026, 1, 15, tzinfo=UTC)
    iso_labelled = _stored_result(title="T", snippet="Published on 2026-03-04 by the ministry.")
    assert extract_published_at(iso_labelled) == datetime(2026, 3, 4, tzinfo=UTC)
    # No date at all stays UNKNOWN.
    assert extract_published_at(_stored_result(title="T", snippet="No dates here.")) is None


def test_freshness_windows_for_signal_claim_types() -> None:
    from app.services.evidence.freshness import freshness_deadline

    now = datetime(2026, 10, 7, tzinfo=UTC)
    assert freshness_deadline("career", now) == now + timedelta(days=14)
    # `policy` has no window of its own: the documented default applies.
    assert freshness_deadline("policy", now) == now + timedelta(days=30)


def test_career_and_policy_claims_stay_evidence_only() -> None:
    """Signal keys must never reach requirements sync or the LLM vocabulary:
    they have no requirement home (documented deferral in llm_extract)."""
    from app.services.evidence.llm_extract import CLAIM_TYPES, KNOWN_CLAIM_KEYS
    from app.services.evidence.requirements import MANDATORY_CLAIM_KEYS
    from app.services.research.career import CAREER_CLAIM_KEY
    from app.services.research.policy import POLICY_CLAIM_KEY

    assert CAREER_CLAIM_KEY not in KNOWN_CLAIM_KEYS
    assert POLICY_CLAIM_KEY not in KNOWN_CLAIM_KEYS
    assert "scholarship_availability" not in KNOWN_CLAIM_KEYS
    assert CAREER_CLAIM_KEY not in CLAIM_TYPES
    assert POLICY_CLAIM_KEY not in CLAIM_TYPES
    # Mandatory gating is a property of requirement keys, all of which the
    # evaluator understands.
    assert MANDATORY_CLAIM_KEYS <= KNOWN_CLAIM_KEYS
    assert MANDATORY_CLAIM_KEYS == {
        "ielts_overall_min",
        "academic_cgpa_min",
        "prerequisite_subjects",
        "application_deadline",
    }
