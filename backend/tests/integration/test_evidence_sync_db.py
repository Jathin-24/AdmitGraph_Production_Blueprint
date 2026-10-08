"""Requirements sync: mandatory flags, NEEDS_VERIFICATION and version history.

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
    Institution,
    Program,
    Requirement,
    RequirementStatus,
    RequirementVersion,
    RunStatus,
    SearchResult,
    SearchRun,
    Source,
    SourceAuthority,
)


async def _seed_program(session: Any, tag: str) -> Program:
    institution = Institution(
        canonical_name=f"Sync University {tag}",
        normalized_name=f"sync university {tag}",
        domain=f"sync-{tag}.example.edu",
    )
    session.add(institution)
    await session.flush()
    program = Program(
        institution_id=institution.id,
        canonical_name=f"M.Sc. Sync Testing {tag}",
        normalized_name=f"m.sc. sync testing {tag}",
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
    claim_type: str = "academic",
    age_days: int = 0,
    status: EvidenceStatus = EvidenceStatus.CURRENT,
    domain: str,
) -> Evidence:
    now = datetime.now(UTC) - timedelta(days=age_days)
    source = Source(
        url=f"https://{domain}/page",
        canonical_url=f"https://{domain}/page-{uuid.uuid4().hex[:6]}",
        domain=domain,
        title=f"Source {domain}",
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
        title="M.Sc. Sync Testing",
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
        status=status,
        retrieved_at=now,
        freshness_deadline=now + timedelta(days=30),
        extraction_version="v1",
    )
    session.add(evidence)
    await session.flush()
    return evidence


async def _versions(session: Any, requirement_id: uuid.UUID) -> list[RequirementVersion]:
    rows = (
        await session.execute(
            select(RequirementVersion)
            .where(RequirementVersion.requirement_id == requirement_id)
            .order_by(RequirementVersion.valid_from.asc(), RequirementVersion.id.asc())
        )
    ).scalars().all()
    return list(rows)


async def _requirement(session: Any, program_id: uuid.UUID, key: str) -> Requirement | None:
    return (
        (
            await session.execute(
                select(Requirement).where(
                    Requirement.program_id == program_id, Requirement.normalized_key == key
                )
            )
        )
        .scalars()
        .first()
    )


async def test_sync_marks_mandatory_keys_and_updates_existing_rows(db_session: Any) -> None:
    """MANDATORY #7: prerequisite_subjects + application_deadline gate admission,
    and `mandatory` is corrected on rows that already exist (create-only writes
    left legacy flags wrong)."""
    from app.services.evidence.requirements import sync_requirements_from_evidence

    tag = uuid.uuid4().hex[:8]
    program = await _seed_program(db_session, tag)

    # Legacy row: written by an older sync that only knew two mandatory keys.
    legacy = Requirement(
        program_id=program.id,
        requirement_type="deadline",
        title="Application Deadline",
        normalized_key="application_deadline",
        operator="eq",
        value={"date": "2026-12-01"},
        mandatory=False,
        status=RequirementStatus.UNKNOWN,
    )
    db_session.add(legacy)
    await db_session.flush()

    deadline_ev = await _seed_evidence(
        db_session,
        program_id=program.id,
        normalized_key="application_deadline",
        value={"date": "2027-01-15"},
        claim_type="deadline",
        domain=f"mandatory-{tag}.example.edu",
    )
    prereq_ev = await _seed_evidence(
        db_session,
        program_id=program.id,
        normalized_key="prerequisite_subjects",
        value={"subjects": ["mathematics", "physics"]},
        claim_type="prerequisite",
        domain=f"prereq-{tag}.example.edu",
    )
    await db_session.commit()

    synced = await sync_requirements_from_evidence(db_session)
    # The scratch DB is shared across tests: only this program's outcome is
    # asserted strictly, global counters are best-effort.
    assert synced["requirements_created"] >= 1  # prerequisite_subjects
    assert synced["requirements_updated"] >= 1  # application_deadline
    assert synced["requirement_versions_created"] >= 1  # its value changed

    deadline_req = await _requirement(db_session, program.id, "application_deadline")
    assert deadline_req is not None
    assert deadline_req.mandatory is True, "existing rows must be corrected, not just created"
    assert deadline_req.value == {"date": "2027-01-15"}
    prereq_req = await _requirement(db_session, program.id, "prerequisite_subjects")
    assert prereq_req is not None
    assert prereq_req.mandatory is True

    versions = await _versions(db_session, deadline_req.id)
    assert len(versions) == 1
    assert versions[0].value == {"date": "2027-01-15"}
    assert versions[0].evidence_id == deadline_ev.id
    assert versions[0].change_reason is not None and "value changed" in versions[0].change_reason

    # Unchanged evidence -> no new history for this requirement.
    await sync_requirements_from_evidence(db_session)
    assert len(await _versions(db_session, deadline_req.id)) == 1
    assert prereq_ev.id is not None and deadline_ev.id is not None


async def test_sync_flags_stale_evidence_needs_verification_with_version(
    db_session: Any,
) -> None:
    """NEEDS_VERIFICATION #9: stale (past freshness_deadline), non-conflicting
    support asks for a re-check; the status change is versioned."""
    from app.services.evidence.requirements import sync_requirements_from_evidence

    tag = uuid.uuid4().hex[:8]
    program = await _seed_program(db_session, tag)
    evidence = await _seed_evidence(
        db_session,
        program_id=program.id,
        normalized_key="ielts_overall_min",
        value={"min": 6.5, "test": "IELTS"},
        claim_type="language",
        domain=f"stale-{tag}.example.edu",
    )
    await db_session.commit()

    first = await sync_requirements_from_evidence(db_session)
    assert first["requirements_created"] >= 1
    req = await _requirement(db_session, program.id, "ielts_overall_min")
    assert req is not None
    assert req.status == RequirementStatus.UNKNOWN, "fresh evidence needs no re-verification"
    assert req.mandatory is True
    assert await _versions(db_session, req.id) == [], "creation is not a change: no history yet"

    # Age the support past its freshness deadline (as the sweep would).
    evidence.freshness_deadline = datetime.now(UTC) - timedelta(days=1)
    await db_session.commit()

    second = await sync_requirements_from_evidence(db_session)
    assert second["requirements_updated"] >= 1
    assert second["requirement_versions_created"] >= 1
    req = await _requirement(db_session, program.id, "ielts_overall_min")
    assert req is not None
    assert req.status == RequirementStatus.NEEDS_VERIFICATION

    versions = await _versions(db_session, req.id)
    assert len(versions) == 1
    assert versions[0].status == RequirementStatus.NEEDS_VERIFICATION
    assert versions[0].evidence_id == evidence.id
    assert versions[0].change_reason is not None
    assert "NEEDS_VERIFICATION" in versions[0].change_reason
    assert versions[0].valid_to is None, "the newest version stays open"

    # Idempotent: still stale -> no duplicated history for this requirement.
    await sync_requirements_from_evidence(db_session)
    assert len(await _versions(db_session, req.id)) == 1


async def test_sync_never_overrides_conflicting_evidence_with_needs_verification(
    db_session: Any,
) -> None:
    """Stale AND conflicting: the conflict stays the story (surfaced by conflict
    detection), so sync must not paper over it with NEEDS_VERIFICATION."""
    from app.services.evidence.conflicts import ConflictDetectionService
    from app.services.evidence.requirements import sync_requirements_from_evidence

    tag = uuid.uuid4().hex[:8]
    program = await _seed_program(db_session, tag)
    older = await _seed_evidence(
        db_session,
        program_id=program.id,
        normalized_key="tuition_max",
        value={"amount": 15000, "currency": "EUR"},
        claim_type="tuition",
        age_days=40,
        domain=f"conflict-a-{tag}.example.edu",
    )
    newer = await _seed_evidence(
        db_session,
        program_id=program.id,
        normalized_key="tuition_max",
        value={"amount": 18000, "currency": "EUR"},
        claim_type="tuition",
        age_days=30,
        domain=f"conflict-b-{tag}.example.edu",
    )
    older.freshness_deadline = datetime.now(UTC) - timedelta(days=10)
    newer.freshness_deadline = datetime.now(UTC) - timedelta(days=1)
    await db_session.commit()

    conflicts = await ConflictDetectionService().detect_for_subject(
        db_session, "program", program.id
    )
    assert len(conflicts) == 1

    synced = await sync_requirements_from_evidence(db_session)
    req = await _requirement(db_session, program.id, "tuition_max")
    assert req is not None
    assert synced["requirements_created"] + synced["requirements_updated"] >= 1
    assert req.status != RequirementStatus.NEEDS_VERIFICATION
    assert await _versions(db_session, req.id) == [], "a conflict is never rewritten as stale"
