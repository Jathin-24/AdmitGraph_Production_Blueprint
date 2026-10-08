"""Conflict authority preference (#17) and the user-facing resolve endpoint.

Integration tests against real PostgreSQL (fixture: tests/conftest.py).
Detection records WHICH member the system would trust by default (highest
Source.source_authority, ties broken by newest retrieval) but leaves the group
UNRESOLVED — resolving it is the user's call, made through the endpoint.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.db.models import (
    ConfidenceLevel,
    Evidence,
    EvidenceConflict,
    EvidenceConflictMember,
    EvidenceStatus,
    Institution,
    Program,
    RunStatus,
    SearchResult,
    SearchRun,
    Source,
    SourceAuthority,
)


@pytest.fixture
async def api(db_session: Any) -> AsyncClient:
    """HTTP client wired to the ASGI app (same scratch database as db_session)."""
    from app.main import app

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        yield client


async def _seed_program(session: Any, tag: str) -> Program:
    institution = Institution(
        canonical_name=f"Conflict University {tag}",
        normalized_name=f"conflict university {tag}",
        domain=f"conflict-{tag}.example.test",
    )
    session.add(institution)
    await session.flush()
    program = Program(
        institution_id=institution.id,
        canonical_name=f"M.Sc. Conflict Signals {tag}",
        normalized_name=f"m.sc. conflict signals {tag}",
    )
    session.add(program)
    await session.flush()
    return program


async def _seed_evidence(
    session: Any,
    *,
    program_id: uuid.UUID,
    normalized_key: str,
    value: dict[str, Any],
    domain: str,
    authority: SourceAuthority,
    age_days: int,
    claim_type: str = "language",
) -> Evidence:
    now = datetime.now(UTC) - timedelta(days=age_days)
    source = Source(
        url=f"https://{domain}/page",
        canonical_url=f"https://{domain}/page-{uuid.uuid4().hex[:6]}",
        domain=domain,
        title=f"Source {domain}",
        source_authority=authority,
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
        title="M.Sc. Conflict Signals",
        snippet="Page states a requirement.",
        result_url=source.url,
        raw_payload={},
        retrieved_at=now,
    )
    session.add(result)
    await session.flush()
    evidence = Evidence(
        source_id=source.id,
        search_result_id=result.id,
        claim_type=claim_type,
        subject_type="program",
        subject_id=program_id,
        claim=f"Page states {normalized_key}.",
        normalized_claim=normalized_key,
        snippet="Page states a requirement.",
        extracted_value=value,
        confidence=ConfidenceLevel.HIGH,
        status=EvidenceStatus.CURRENT,
        retrieved_at=now,
        freshness_deadline=now + timedelta(days=30),
        extraction_version="v1",
    )
    session.add(evidence)
    await session.flush()
    return evidence


async def _detect_conflict(
    db_session: Any, program_id: uuid.UUID
) -> EvidenceConflict:
    from app.services.evidence.conflicts import ConflictDetectionService

    conflicts = await ConflictDetectionService().detect_for_subject(
        db_session, "program", program_id
    )
    assert len(conflicts) == 1
    return conflicts[0]


async def _reload_conflict(db_session: Any, conflict_id: uuid.UUID) -> EvidenceConflict:
    """Re-read a conflict through THIS session after the API's own session
    wrote it (populate_existing: never trust a stale identity map)."""
    row = (
        (
            await db_session.execute(
                select(EvidenceConflict)
                .where(EvidenceConflict.id == conflict_id)
                .execution_options(populate_existing=True)
            )
        )
        .scalars()
        .first()
    )
    assert row is not None
    return row


async def _seed_conflicting_pair(db_session: Any, tag: str) -> tuple[Program, Evidence, Evidence]:
    """Older OFFICIAL claim vs fresher FORUM claim with differing values."""
    program = await _seed_program(db_session, tag)
    official = await _seed_evidence(
        db_session,
        program_id=program.id,
        normalized_key="ielts_overall_min",
        value={"min": 6.5, "test": "IELTS"},
        domain=f"official-{tag}.example.test",
        authority=SourceAuthority.OFFICIAL_UNIVERSITY,
        age_days=40,
    )
    forum = await _seed_evidence(
        db_session,
        program_id=program.id,
        normalized_key="ielts_overall_min",
        value={"min": 7.0, "test": "IELTS"},
        domain=f"forum-{tag}.example.test",
        authority=SourceAuthority.FORUM_SOCIAL,
        age_days=5,
    )
    await db_session.commit()
    return program, official, forum


async def test_detection_prefers_authority_member_but_stays_unresolved(
    db_session: Any,
) -> None:
    """#17 part 1: detection records the member an official channel backs as
    the default preference, while the conflict itself stays UNRESOLVED —
    a preference is a hint, never a silent verdict."""
    tag = uuid.uuid4().hex[:8]
    program, official, forum = await _seed_conflicting_pair(db_session, tag)

    conflict = await _detect_conflict(db_session, program.id)

    assert conflict.preferred_evidence_id == official.id, (
        "authority must beat recency: the OFFICIAL_UNIVERSITY claim is preferred "
        "even though the FORUM_SOCIAL claim is fresher"
    )
    assert conflict.resolution_status == "UNRESOLVED"
    assert conflict.resolved_at is None
    assert conflict.resolution_reason is None

    # Both claims are kept, flagged and downgraded (never overwritten).
    rows = (
        (
            await db_session.execute(
                select(Evidence).where(Evidence.subject_id == program.id)
            )
        )
        .scalars()
        .all()
    )
    assert {r.id for r in rows} == {official.id, forum.id}
    assert {r.status for r in rows} == {EvidenceStatus.CONFLICTING}
    assert {r.confidence for r in rows} == {ConfidenceLevel.LOW}

    members = (
        (
            await db_session.execute(
                select(EvidenceConflictMember).where(
                    EvidenceConflictMember.conflict_id == conflict.id
                )
            )
        )
        .scalars()
        .all()
    )
    assert {m.evidence_id for m in members} == {official.id, forum.id}


async def test_detection_tie_broken_by_newest_retrieval(db_session: Any) -> None:
    """Equal authority -> the fresher retrieval is the default preference."""
    tag = uuid.uuid4().hex[:8]
    program = await _seed_program(db_session, tag)
    older = await _seed_evidence(
        db_session,
        program_id=program.id,
        normalized_key="tuition_max",
        value={"amount": 15000, "currency": "EUR"},
        domain=f"tie-a-{tag}.example.test",
        authority=SourceAuthority.OFFICIAL_UNIVERSITY,
        age_days=30,
        claim_type="tuition",
    )
    newer = await _seed_evidence(
        db_session,
        program_id=program.id,
        normalized_key="tuition_max",
        value={"amount": 18000, "currency": "EUR"},
        domain=f"tie-b-{tag}.example.test",
        authority=SourceAuthority.OFFICIAL_UNIVERSITY,
        age_days=3,
        claim_type="tuition",
    )
    await db_session.commit()

    conflict = await _detect_conflict(db_session, program.id)

    assert conflict.preferred_evidence_id == newer.id
    assert conflict.resolution_status == "UNRESOLVED"
    assert older.id != newer.id


async def test_resolve_endpoint_stores_explicit_choice(
    db_session: Any, api: AsyncClient
) -> None:
    """#17 part 2: the user picks a member and states a reason; only members
    of THIS conflict may be chosen."""
    tag = uuid.uuid4().hex[:8]
    program, official, forum = await _seed_conflicting_pair(db_session, tag)
    official_id = official.id
    forum_id = forum.id
    program_id = program.id
    conflict = await _detect_conflict(db_session, program.id)
    await db_session.commit()

    response = await api.post(
        f"/api/v1/evidence/conflicts/{conflict.id}/resolve",
        json={"preferred_evidence_id": str(forum_id), "reason": "Checked the course page"},
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["id"] == str(conflict.id)
    assert payload["resolution_status"] == "RESOLVED"
    assert payload["preferred_evidence_id"] == str(forum_id)
    assert payload["resolution_reason"] == "Checked the course page"
    assert payload["resolved_at"] is not None

    # The choice is visible through GET /evidence/{id}/conflicts too.
    listed = await api.get(f"/api/v1/evidence/{official_id}/conflicts")
    assert listed.status_code == 200, listed.text
    shown = listed.json()["conflicts"][0]
    assert shown["resolution_status"] == "RESOLVED"
    assert shown["preferred_evidence_id"] == str(forum_id)
    assert shown["resolution_reason"] == "Checked the course page"

    # Evidence rows keep their flag: resolution never rewrites the claims.
    rows = (
        (await db_session.execute(select(Evidence).where(Evidence.subject_id == program_id)))
        .scalars()
        .all()
    )
    assert {r.status for r in rows} == {EvidenceStatus.CONFLICTING}


async def test_resolve_without_body_accepts_authority_default(
    db_session: Any, api: AsyncClient
) -> None:
    """Resolving with no body accepts the preference detection recorded."""
    tag = uuid.uuid4().hex[:8]
    program, official, _forum = await _seed_conflicting_pair(db_session, tag)
    official_id = official.id
    conflict_id = (await _detect_conflict(db_session, program.id)).id
    await db_session.commit()

    response = await api.post(f"/api/v1/evidence/conflicts/{conflict_id}/resolve")
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["resolution_status"] == "RESOLVED"
    assert payload["preferred_evidence_id"] == str(official_id)
    assert payload["resolution_reason"] is None

    stored = await _reload_conflict(db_session, conflict_id)
    assert stored.preferred_evidence_id == official_id
    assert stored.resolved_at is not None


async def test_resolve_endpoint_rejects_bad_requests(
    db_session: Any, api: AsyncClient
) -> None:
    """404 for unknown conflict/evidence ids, 409 for non-members and for
    conflicts that are already resolved."""
    tag = uuid.uuid4().hex[:8]
    program, official, forum = await _seed_conflicting_pair(db_session, tag)
    official_id = official.id
    forum_id = forum.id
    # A separate, non-conflicting claim: exists but is not a member.
    outsider = await _seed_evidence(
        db_session,
        program_id=program.id,
        normalized_key="application_deadline",
        value={"date": "2027-01-15"},
        domain=f"outsider-{tag}.example.test",
        authority=SourceAuthority.OFFICIAL_UNIVERSITY,
        age_days=10,
        claim_type="deadline",
    )
    outsider_id = outsider.id
    conflict_id = (await _detect_conflict(db_session, program.id)).id
    await db_session.commit()

    unknown_conflict = await api.post(
        f"/api/v1/evidence/conflicts/{uuid.uuid4()}/resolve", json={}
    )
    assert unknown_conflict.status_code == 404
    assert unknown_conflict.json()["error"]["code"] == "NOT_FOUND"

    unknown_evidence = await api.post(
        f"/api/v1/evidence/conflicts/{conflict_id}/resolve",
        json={"preferred_evidence_id": str(uuid.uuid4())},
    )
    assert unknown_evidence.status_code == 404
    assert unknown_evidence.json()["error"]["code"] == "NOT_FOUND"

    non_member = await api.post(
        f"/api/v1/evidence/conflicts/{conflict_id}/resolve",
        json={"preferred_evidence_id": str(outsider_id)},
    )
    assert non_member.status_code == 409
    assert non_member.json()["error"]["code"] == "EVIDENCE_NOT_IN_CONFLICT"

    resolved = await api.post(
        f"/api/v1/evidence/conflicts/{conflict_id}/resolve",
        json={"preferred_evidence_id": str(official_id), "reason": "first call"},
    )
    assert resolved.status_code == 200, resolved.text

    again = await api.post(
        f"/api/v1/evidence/conflicts/{conflict_id}/resolve",
        json={"preferred_evidence_id": str(forum_id)},
    )
    assert again.status_code == 409
    assert again.json()["error"]["code"] == "ALREADY_RESOLVED"

    # The first resolution stands: the second call changed nothing.
    stored = await _reload_conflict(db_session, conflict_id)
    assert stored.preferred_evidence_id == official_id
    assert stored.resolution_reason == "first call"


async def test_resolve_endpoint_rejects_conflict_without_members(
    db_session: Any, api: AsyncClient
) -> None:
    """A conflict group with no evidence members has nothing to prefer."""
    conflict = EvidenceConflict(
        conflict_key=f"program:{uuid.uuid4()}:orphan",
        description="Group whose members were removed",
    )
    db_session.add(conflict)
    await db_session.commit()

    response = await api.post(f"/api/v1/evidence/conflicts/{conflict.id}/resolve", json={})
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "EMPTY_CONFLICT"
