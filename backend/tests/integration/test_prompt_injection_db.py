"""P2-25 (DB side): low-trust support cannot assert a requirement as fact.

A requirement whose freshest value comes from a forum thread or an
unclassifiable domain is flagged NEEDS_VERIFICATION by the sync pass
(app/services/evidence/requirements.py, LOW_TRUST_AUTHORITIES) instead of
passing silently — while official, fresh support keeps its own status.

Integration test against real PostgreSQL (fixture: tests/conftest.py).
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
    Source,
    SourceAuthority,
)


async def _seed_program(session: Any, tag: str) -> Program:
    institution = Institution(
        canonical_name=f"Hardening University {tag}",
        normalized_name=f"hardening university {tag}",
        domain=f"hardening-{tag}.example.edu",
    )
    session.add(institution)
    await session.flush()
    program = Program(
        institution_id=institution.id,
        canonical_name=f"M.Sc. Hardening {tag}",
        normalized_name=f"m.sc. hardening {tag}",
    )
    session.add(program)
    await session.flush()
    return program


async def _seed_evidence(
    session: Any,
    *,
    program_id: uuid.UUID,
    authority: SourceAuthority,
    domain: str,
    value: dict[str, Any] | None = None,
    age_days: int = 0,
) -> Evidence:
    now = datetime.now(UTC) - timedelta(days=age_days)
    source = Source(
        url=f"https://{domain}/programme",
        canonical_url=f"https://{domain}/programme-{uuid.uuid4().hex[:6]}",
        domain=domain,
        title=f"Source {domain}",
        source_authority=authority,
        last_seen_at=now,
    )
    session.add(source)
    await session.flush()
    evidence = Evidence(
        source_id=source.id,
        claim_type="language",
        subject_type="program",
        subject_id=program_id,
        claim="Page states IELTS 6.5 overall required.",
        normalized_claim="ielts_overall_min",
        extracted_value=value or {"min": 6.5, "test": "IELTS"},
        confidence=ConfidenceLevel.HIGH,
        status=EvidenceStatus.CURRENT,
        retrieved_at=now,
        freshness_deadline=now + timedelta(days=30),
        extraction_version="v1",
    )
    session.add(evidence)
    await session.flush()
    return evidence


async def _requirement(session: Any, program_id: uuid.UUID) -> Requirement | None:
    return (
        (
            await session.execute(
                select(Requirement).where(
                    Requirement.program_id == program_id,
                    Requirement.normalized_key == "ielts_overall_min",
                )
            )
        )
        .scalars()
        .first()
    )


async def test_forum_backed_requirement_is_flagged_needs_verification(db_session: Any) -> None:
    from app.services.evidence.requirements import sync_requirements_from_evidence

    tag = uuid.uuid4().hex[:8]
    forum_program = await _seed_program(db_session, f"f{tag}")
    official_program = await _seed_program(db_session, f"o{tag}")
    # Captured before expire_all(): refreshing a detached handle would do IO
    # outside the greenlet context.
    forum_id, official_id = forum_program.id, official_program.id
    await _seed_evidence(
        db_session,
        program_id=forum_id,
        authority=SourceAuthority.FORUM_SOCIAL,
        domain=f"reddit-claim-{tag}.com",
    )
    await _seed_evidence(
        db_session,
        program_id=official_id,
        authority=SourceAuthority.OFFICIAL_UNIVERSITY,
        domain=f"official-claim-{tag}.example.edu",
    )
    await db_session.commit()

    await sync_requirements_from_evidence(db_session)
    db_session.expire_all()

    forum_req = await _requirement(db_session, forum_id)
    assert forum_req is not None, "the forum-backed claim must still mint a requirement"
    assert forum_req.status == RequirementStatus.NEEDS_VERIFICATION, (
        "low-trust support (P2-25) must ask for a fresh check, not assert the value"
    )
    assert forum_req.value == {"min": 6.5, "test": "IELTS"}, "the value is kept, just flagged"

    official_req = await _requirement(db_session, official_id)
    assert official_req is not None
    assert official_req.status != RequirementStatus.NEEDS_VERIFICATION, (
        "official, fresh, uncontested support must not be flagged"
    )


async def test_freshest_low_trust_row_wins_and_carries_the_flag(db_session: Any) -> None:
    """Support selection is freshest-first: when a newer forum row supersedes
    an older official one, BOTH the value and the flag come from the forum row
    — an old trustworthy page must not launder a low-trust update."""
    from app.services.evidence.requirements import sync_requirements_from_evidence

    tag = uuid.uuid4().hex[:8]
    program = await _seed_program(db_session, tag)
    program_id = program.id  # before expire_all() (detached handles can't do IO)
    await _seed_evidence(
        db_session,
        program_id=program_id,
        authority=SourceAuthority.OFFICIAL_UNIVERSITY,
        domain=f"official-older-{tag}.example.edu",
        value={"min": 6.5, "test": "IELTS"},
        age_days=5,
    )
    await _seed_evidence(
        db_session,
        program_id=program_id,
        authority=SourceAuthority.FORUM_SOCIAL,
        domain=f"reddit-newer-{tag}.com",
        value={"min": 7.0, "test": "IELTS"},
        age_days=0,
    )
    await db_session.commit()

    await sync_requirements_from_evidence(db_session)
    db_session.expire_all()

    req = await _requirement(db_session, program_id)
    assert req is not None
    assert req.value == {"min": 7.0, "test": "IELTS"}, "the freshest row carries the value"
    assert req.status == RequirementStatus.NEEDS_VERIFICATION
