"""Evidence recording: de-duplication, honest refresh and publication dates.

Integration tests against real PostgreSQL (fixture: tests/conftest.py).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select

from app.db.models import (
    ConfidenceLevel,
    Evidence,
    EvidenceStatus,
    RunStatus,
    SearchResult,
    SearchRun,
    Source,
    SourceAuthority,
)


async def _seed_result(
    session: Any, *, domain: str = "dedupe.example.edu", raw_payload: dict[str, Any] | None = None
) -> tuple[Source, SearchResult]:
    now = datetime.now(UTC)
    source = Source(
        url=f"https://{domain}/page",
        canonical_url=f"https://{domain}/page-{uuid.uuid4().hex[:6]}",
        domain=domain,
        title="Dedupe Source",
        source_authority=SourceAuthority.OFFICIAL_UNIVERSITY,
        last_seen_at=now,
    )
    session.add(source)
    await session.flush()
    run = SearchRun(engine="google", query="q", parameters={}, status=RunStatus.SUCCEEDED)
    session.add(run)
    await session.flush()
    result = SearchResult(
        search_run_id=run.id,
        source_id=source.id,
        position=1,
        result_type="google",
        title="M.Sc. Dedupe Testing",
        snippet="Page states IELTS 6.5 overall required.",
        result_url=source.url,
        raw_payload=raw_payload or {},
        retrieved_at=now,
    )
    session.add(result)
    await session.flush()
    return source, result


def _ielts_claim() -> Any:
    from app.services.evidence.extraction import ExtractedClaim

    return ExtractedClaim(
        claim_type="language",
        normalized_key="ielts_overall_min",
        value={"min": 6.5, "test": "IELTS"},
        claim="Page states IELTS 6.5 overall required.",
        confidence=ConfidenceLevel.HIGH,
        subject_id=uuid.uuid4(),
    )


async def test_same_claim_twice_stores_one_row_and_refreshes(db_session: Any) -> None:
    """A re-observed claim refreshes the row instead of appending a duplicate
    (evidence/conflict growth must stay bounded across runs)."""
    from app.services.evidence.extraction import EvidenceExtractionService

    source, result = await _seed_result(db_session)
    service = EvidenceExtractionService()

    first = await service.record_claims(db_session, source, result, [_ielts_claim()])
    await db_session.commit()
    assert len(first) == 1
    row_id = first[0].id

    # Age the row so the refresh is observable.
    row = await db_session.get(Evidence, row_id)
    assert row is not None
    row.retrieved_at = datetime.now(UTC) - timedelta(days=40)
    row.freshness_deadline = datetime.now(UTC) - timedelta(days=10)
    row.status = EvidenceStatus.STALE
    await db_session.commit()

    second = await service.record_claims(db_session, source, result, [_ielts_claim()])
    await db_session.commit()
    # Same claim -> same row returned, refreshed to the stored result's time.
    assert len(second) == 1
    assert second[0].id == row_id

    rows = (
        (
            await db_session.execute(
                select(Evidence).where(
                    Evidence.search_result_id == result.id,
                    Evidence.normalized_claim == "ielts_overall_min",
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(rows) == 1, "an identical re-observation must never append a row"
    assert rows[0].retrieved_at == result.retrieved_at
    assert rows[0].freshness_deadline == result.retrieved_at + timedelta(days=30)
    assert rows[0].status == EvidenceStatus.CURRENT, "re-observed inside the window"


async def test_different_values_still_insert_conflicting_claims(db_session: Any) -> None:
    """Conflicts are a feature: a differing value must NOT be deduplicated."""
    from app.services.evidence.extraction import EvidenceExtractionService, ExtractedClaim

    source, result = await _seed_result(db_session)
    service = EvidenceExtractionService()
    subject_id = uuid.uuid4()
    claim = _ielts_claim()
    claim.subject_id = subject_id

    other = ExtractedClaim(
        claim_type="language",
        normalized_key="ielts_overall_min",
        value={"min": 7.0, "test": "IELTS"},
        claim="Another page states IELTS 7.0 overall required.",
        confidence=ConfidenceLevel.MEDIUM,
        subject_id=subject_id,
    )
    stored = await service.record_claims(db_session, source, result, [claim, other])
    await db_session.commit()
    assert len(stored) == 2
    rows = (
        (
            await db_session.execute(
                select(Evidence).where(
                    Evidence.search_result_id == result.id,
                    Evidence.normalized_claim == "ielts_overall_min",
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(rows) == 2


async def test_published_at_populated_only_when_result_carries_a_date(
    db_session: Any,
) -> None:
    from app.services.evidence.extraction import EvidenceExtractionService

    source, dated = await _seed_result(
        db_session, raw_payload={"date": "2026-03-04"}, domain="dated.example.edu"
    )
    _, undated = await _seed_result(db_session, domain="undated.example.edu")
    service = EvidenceExtractionService()

    stored = await service.record_claims(db_session, source, dated, [_ielts_claim()])
    stored += await service.record_claims(db_session, source, undated, [_ielts_claim()])
    await db_session.commit()
    assert len(stored) == 2

    dated_row = await db_session.get(Evidence, stored[0].id)
    undated_row = await db_session.get(Evidence, stored[1].id)
    assert dated_row is not None and undated_row is not None
    assert dated_row.published_at == datetime(2026, 3, 4, tzinfo=UTC)
    assert undated_row.published_at is None, "no real date on the result: never guessed"
