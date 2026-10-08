"""Career/policy signal evidence (#19): stored rows only, evidence-only keys.

Integration tests against real PostgreSQL (fixture: tests/conftest.py).
No live search is ever issued: the recording functions read search_runs /
search_results rows that already exist and turn them into Evidence with full
provenance, conservative subject binding and honest confidence/freshness.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select

from app.db.models import (
    Evidence,
    Institution,
    Program,
    Requirement,
    RunStatus,
    SearchResult,
    SearchRun,
    Source,
    SourceAuthority,
)


async def _seed_run(
    session: Any,
    *,
    engine: str,
    purpose: str,
    query: str,
    result_titles: list[str],
    domain: str,
    authority: SourceAuthority,
    institution: Institution | None = None,
) -> SearchRun:
    now = datetime.now(UTC)
    run = SearchRun(
        engine=engine,
        query=query,
        parameters={"purpose": purpose},
        status=RunStatus.SUCCEEDED,
        result_count=len(result_titles),
    )
    session.add(run)
    await session.flush()
    for position, title in enumerate(result_titles, start=1):
        source = Source(
            url=f"https://{domain}/page-{position}",
            canonical_url=f"https://{domain}/page-{position}-{uuid.uuid4().hex[:6]}",
            domain=domain,
            title=title,
            source_authority=authority,
            last_seen_at=now,
        )
        session.add(source)
        await session.flush()
        session.add(
            SearchResult(
                search_run_id=run.id,
                source_id=source.id,
                position=position,
                result_type=engine,
                title=title,
                snippet=f"Stored {engine} result for '{query}'.",
                result_url=source.url,
                raw_payload={},
                retrieved_at=now,
            )
        )
    await session.flush()
    return run


async def _seed_program(session: Any, tag: str) -> Program:
    institution = Institution(
        canonical_name=f"Signals University {tag}",
        normalized_name=f"signals university {tag}",
        domain=f"signals-{tag}.example.test",
    )
    session.add(institution)
    await session.flush()
    program = Program(
        institution_id=institution.id,
        canonical_name=f"M.Sc. Signals Testing {tag}",
        normalized_name=f"m.sc. signals testing {tag}",
    )
    session.add(program)
    await session.flush()
    return program


async def _evidence_rows(session: Any, run_id: uuid.UUID) -> list[Evidence]:
    rows = (
        (
            await session.execute(
                select(Evidence)
                .join(SearchResult, Evidence.search_result_id == SearchResult.id)
                .where(SearchResult.search_run_id == run_id)
            )
        )
        .scalars()
        .all()
    )
    return list(rows)


async def test_career_evidence_binds_unambiguously_with_official_confidence(
    db_session: Any,
) -> None:
    """#19: a google_jobs result whose title uniquely matches a program binds
    to it; official-domain sources earn HIGH confidence and the `career`
    freshness window (14 days) applies."""
    from app.services.research.career import (
        CAREER_CLAIM_KEY,
        record_career_evidence,
    )

    tag = uuid.uuid4().hex[:8]
    program = await _seed_program(db_session, tag)
    run = await _seed_run(
        db_session,
        engine="google_jobs",
        purpose="career",
        query=f"ml engineer {tag}",
        result_titles=[program.canonical_name],
        domain=f"careers-{tag}.example.test",
        authority=SourceAuthority.OFFICIAL_UNIVERSITY,
    )
    await db_session.commit()

    summary = await record_career_evidence(db_session, search_run_ids=[run.id])
    assert summary["career_results_considered"] == 1
    assert summary["career_evidence_recorded"] == 1
    assert summary["career_evidence_bound"] == 1

    rows = await _evidence_rows(db_session, run.id)
    assert len(rows) == 1
    row = rows[0]
    assert row.normalized_claim == CAREER_CLAIM_KEY
    assert row.claim_type == "career"
    assert row.subject_type == "program"
    assert row.subject_id == program.id, "unique title match must bind"
    assert row.confidence.value == "HIGH", "official channel -> HIGH"
    assert row.source_id is not None and row.search_result_id is not None
    assert row.retrieved_at is not None and row.freshness_deadline is not None
    assert row.freshness_deadline == row.retrieved_at + timedelta(days=14)
    value = dict(row.extracted_value or {})
    assert value["engine"] == "google_jobs"
    assert value["query"] == f"ml engineer {tag}"
    assert value["position"] == 1

    # Recording is idempotent: a second pass refreshes, never duplicates.
    again = await record_career_evidence(db_session, search_run_ids=[run.id])
    assert again["career_evidence_recorded"] == 1
    assert len(await _evidence_rows(db_session, run.id)) == 1


async def test_career_evidence_stays_unbound_and_medium_when_unsafe(
    db_session: Any,
) -> None:
    """No unambiguous match -> subject_id stays NULL (never guessed) and a
    non-official source is capped at MEDIUM confidence."""
    from app.services.research.career import (
        CAREER_CLAIM_KEY,
        record_career_evidence,
    )

    tag = uuid.uuid4().hex[:8]
    run = await _seed_run(
        db_session,
        engine="google_jobs",
        purpose="career",
        query=f"data analyst {tag}",
        result_titles=[f"Data Analyst role posting {tag}"],
        domain=f"jobsboard-{tag}.example.test",
        authority=SourceAuthority.CREDIBLE_SECONDARY,
    )
    await db_session.commit()

    summary = await record_career_evidence(db_session, search_run_ids=[run.id])
    assert summary["career_results_considered"] == 1
    assert summary["career_evidence_recorded"] == 1
    assert summary["career_evidence_bound"] == 0, "ambiguous title must not bind"

    rows = await _evidence_rows(db_session, run.id)
    assert len(rows) == 1
    assert rows[0].normalized_claim == CAREER_CLAIM_KEY
    assert rows[0].subject_id is None, "unbound claims stay auditable, not guessed"
    assert rows[0].confidence.value == "MEDIUM", "non-official channel -> MEDIUM"


async def test_policy_evidence_records_with_default_freshness(db_session: Any) -> None:
    """#19: policy results become evidence-only rows; `policy` has no window
    of its own, so the documented 30-day default applies."""
    from app.services.research.policy import POLICY_CLAIM_KEY, record_policy_evidence

    tag = uuid.uuid4().hex[:8]
    program = await _seed_program(db_session, tag)
    run = await _seed_run(
        db_session,
        engine="google",
        purpose="policy",
        query=f"germany student visa financial proof {tag}",
        result_titles=[program.canonical_name],
        domain=f"visa-{tag}.example.test",
        authority=SourceAuthority.OFFICIAL_GOVERNMENT,
    )
    await db_session.commit()

    summary = await record_policy_evidence(db_session, search_run_ids=[run.id])
    assert summary["policy_results_considered"] == 1
    assert summary["policy_evidence_recorded"] == 1
    assert summary["policy_evidence_bound"] == 1

    rows = await _evidence_rows(db_session, run.id)
    assert len(rows) == 1
    row = rows[0]
    assert row.normalized_claim == POLICY_CLAIM_KEY
    assert row.claim_type == "policy"
    assert row.subject_id == program.id
    assert row.confidence.value == "HIGH", "government domain -> HIGH"
    assert row.retrieved_at is not None and row.freshness_deadline is not None
    assert row.freshness_deadline == row.retrieved_at + timedelta(days=30)
    value = dict(row.extracted_value or {})
    assert value["engine"] == "google"
    assert value["query"].startswith("germany student visa")

    # Idempotent: a second pass refreshes rather than appends.
    again = await record_policy_evidence(db_session, search_run_ids=[run.id])
    assert again["policy_evidence_recorded"] == 1
    assert len(await _evidence_rows(db_session, run.id)) == 1


async def test_signal_recording_with_no_stored_results_is_honest_zeros(
    db_session: Any,
) -> None:
    """A failed/skipped run has no stored rows -> honest zeros, no claims."""
    from app.services.research.career import record_career_evidence
    from app.services.research.policy import record_policy_evidence

    summary_c = await record_career_evidence(db_session, search_run_ids=[uuid.uuid4()])
    summary_p = await record_policy_evidence(db_session, search_run_ids=[uuid.uuid4()])
    assert summary_c == {
        "career_results_considered": 0,
        "career_evidence_recorded": 0,
        "career_evidence_bound": 0,
    }
    assert summary_p == {
        "policy_results_considered": 0,
        "policy_evidence_recorded": 0,
        "policy_evidence_bound": 0,
    }


async def test_signal_evidence_never_creates_requirement_rows(db_session: Any) -> None:
    """Evidence-only keys have no requirement home: requirements sync must
    never mint a Requirement for them (documented deferral, not a miss)."""
    from app.services.evidence.requirements import sync_requirements_from_evidence
    from app.services.research.career import CAREER_CLAIM_KEY, record_career_evidence
    from app.services.research.policy import POLICY_CLAIM_KEY, record_policy_evidence

    tag = uuid.uuid4().hex[:8]
    program = await _seed_program(db_session, tag)
    career_run = await _seed_run(
        db_session,
        engine="google_jobs",
        purpose="career",
        query=f"ml engineer {tag}",
        result_titles=[program.canonical_name],
        domain=f"careers-{tag}.example.test",
        authority=SourceAuthority.OFFICIAL_UNIVERSITY,
    )
    policy_run = await _seed_run(
        db_session,
        engine="google",
        purpose="policy",
        query=f"germany student visa {tag}",
        result_titles=[program.canonical_name],
        domain=f"visa-{tag}.example.test",
        authority=SourceAuthority.OFFICIAL_GOVERNMENT,
    )
    await db_session.commit()

    career = await record_career_evidence(db_session, search_run_ids=[career_run.id])
    policy = await record_policy_evidence(db_session, search_run_ids=[policy_run.id])
    assert career["career_evidence_recorded"] == 1
    assert policy["policy_evidence_recorded"] == 1

    await sync_requirements_from_evidence(db_session)

    signal_requirements = (
        (
            await db_session.execute(
                select(Requirement).where(
                    Requirement.program_id == program.id,
                    Requirement.normalized_key.in_([CAREER_CLAIM_KEY, POLICY_CLAIM_KEY]),
                )
            )
        )
        .scalars()
        .all()
    )
    assert list(signal_requirements) == [], (
        "career/policy keys have no evaluator home: they must stay evidence-only"
    )

    # The signals themselves are still visible as evidence for the program.
    bound = (
        (
            await db_session.execute(
                select(Evidence).where(
                    Evidence.subject_id == program.id,
                    Evidence.normalized_claim.in_([CAREER_CLAIM_KEY, POLICY_CLAIM_KEY]),
                )
            )
        )
        .scalars()
        .all()
    )
    assert {e.normalized_claim for e in bound} == {CAREER_CLAIM_KEY, POLICY_CLAIM_KEY}


async def test_official_source_classification_covers_declared_channels() -> None:
    """Confidence HIGH is only ever granted to declared official channels."""
    from app.db.models import Source
    from app.services.research.source_profiles import (
        OFFICIAL_AUTHORITIES,
        is_official_source,
    )

    assert OFFICIAL_AUTHORITIES == {
        SourceAuthority.OFFICIAL_UNIVERSITY,
        SourceAuthority.OFFICIAL_GOVERNMENT,
        SourceAuthority.OFFICIAL_ORGANIZATION,
        SourceAuthority.ACCREDITED_BODY,
    }
    now = datetime.now(UTC)
    official = Source(
        url="https://uni.example.test/x",
        canonical_url=f"https://uni.example.test/x-{uuid.uuid4().hex[:6]}",
        domain="uni.example.test",
        title="University",
        source_authority=SourceAuthority.OFFICIAL_UNIVERSITY,
        last_seen_at=now,
    )
    forum = Source(
        url="https://forum.example.test/x",
        canonical_url=f"https://forum.example.test/x-{uuid.uuid4().hex[:6]}",
        domain="forum.example.test",
        title="Forum",
        source_authority=SourceAuthority.FORUM_SOCIAL,
        last_seen_at=now,
    )
    assert is_official_source(official) is True
    assert is_official_source(forum) is False
    assert is_official_source(None) is False
