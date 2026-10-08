"""Bounded evidence recheck: refresh on confirmation, new row on change.

Integration tests against real PostgreSQL (fixture: tests/conftest.py).
No live provider calls: the SerpApi client and the LLM extraction entry point
are replaced with deterministic fakes (stored rows only).
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


class _FakeSerpApi:
    """Deterministic SerpApi stand-in: returns one organic result."""

    def __init__(self, items: list[dict[str, Any]]) -> None:
        self._items = items

    async def search(
        self,
        engine: str,
        q: str,
        *,
        parameters: dict[str, Any] | None = None,
        cache: dict[str, dict[str, Any]] | None = None,
        locale: dict[str, dict[str, Any]] | None = None,
    ) -> Any:
        from app.services.serpapi.client import SerpApiResult

        return SerpApiResult(
            engine=engine,
            query=q,
            parameters=parameters or {},
            raw={},
            organic_results=self._items,
            search_id="fake-recheck",
            duration_ms=1,
        )


async def _seed_program_with_evidence(
    session: Any, tag: str, *, status: EvidenceStatus = EvidenceStatus.STALE
) -> tuple[Program, Evidence]:
    now = datetime.now(UTC)
    institution = Institution(
        canonical_name=f"Recheck University {tag}",
        normalized_name=f"recheck university {tag}",
        domain=f"recheck-{tag}.example.test",
    )
    session.add(institution)
    await session.flush()
    program = Program(
        institution_id=institution.id,
        canonical_name=f"M.Sc. Recheck Testing {tag}",
        normalized_name=f"m.sc. recheck testing {tag}",
    )
    session.add(program)
    await session.flush()

    source = Source(
        url=f"https://recheck-{tag}.example.test/ielts",
        canonical_url=f"https://recheck-{tag}.example.test/ielts-{uuid.uuid4().hex[:6]}",
        domain=f"recheck-{tag}.example.test",
        title="M.Sc. Recheck Testing",
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
        title="M.Sc. Recheck Testing",
        snippet="Page states IELTS 6.5 overall required.",
        result_url=source.url,
        raw_payload={},
        retrieved_at=now,
    )
    session.add(result)
    await session.flush()
    evidence = Evidence(
        source_id=source.id,
        search_result_id=result.id,
        claim_type="language",
        subject_type="program",
        subject_id=program.id,
        claim="Page states IELTS 6.5 overall required.",
        normalized_claim="ielts_overall_min",
        snippet="Page states IELTS 6.5 overall required.",
        extracted_value={"min": 6.5, "test": "IELTS"},
        confidence=ConfidenceLevel.HIGH,
        status=status,
        retrieved_at=now - timedelta(days=40),
        freshness_deadline=now - timedelta(days=10),
        extraction_version="v1",
    )
    session.add(evidence)
    await session.flush()
    await session.commit()
    return program, evidence


def _fake_extract(value: dict[str, Any]) -> Any:
    """extract_claims stand-in returning one claim for the stored key."""
    from app.services.evidence.llm_extract import LLMClaim, LLMClaims

    async def _run(
        provider: Any,
        *,
        title: str | None,
        snippet: str | None,
        domain: str | None,
        extraction_model: str | None = None,
    ) -> LLMClaims:
        return LLMClaims(
            claims=[
                LLMClaim(
                    claim_type="language",
                    normalized_key="ielts_overall_min",
                    value=dict(value),
                    claim="Program page states a language requirement.",
                    confidence="HIGH",
                )
            ]
        )

    return _run


async def _recheck_runs(session: Any, evidence_id: uuid.UUID) -> list[SearchRun]:
    rows = (
        (
            await session.execute(
                select(SearchRun).where(
                    SearchRun.parameters["purpose"].astext == "recheck",
                    SearchRun.parameters["evidence_id"].astext == str(evidence_id),
                )
            )
        )
        .scalars()
        .all()
    )
    return list(rows)


async def test_recheck_with_same_value_refreshes_stale_evidence(
    db_session: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A fresh observation of the SAME value re-verifies the claim: the row is
    refreshed (STALE -> CURRENT, window recomputed) instead of duplicated."""
    from app.services.evidence.recheck import recheck_evidence

    tag = uuid.uuid4().hex[:8]
    program, evidence = await _seed_program_with_evidence(db_session, tag)
    assert evidence.status == EvidenceStatus.STALE
    old_deadline = evidence.freshness_deadline

    monkeypatch.setattr(
        "app.services.evidence.recheck.extract_claims",
        _fake_extract({"min": 6.5, "test": "IELTS"}),
    )
    fake = _FakeSerpApi(
        [
            {
                "title": f"M.Sc. Recheck Testing - Recheck University {tag}",
                "link": f"https://recheck-{tag}.example.test/ielts",
                "snippet": "Page states IELTS 6.5 overall required.",
            }
        ]
    )

    result = await recheck_evidence(
        db_session, evidence.id, serpapi=fake  # type: ignore[arg-type]
    )

    assert result["recheck"]["found"] is True
    assert result["recheck"]["refreshed"] is True
    assert result["recheck"]["new_evidence_id"] is None
    assert result["status"] == EvidenceStatus.CURRENT.value
    assert result["recheck"]["query"].startswith(program.canonical_name)

    rows = (
        (
            await db_session.execute(
                select(Evidence).where(Evidence.subject_id == program.id)
            )
        )
        .scalars()
        .all()
    )
    assert len(rows) == 1, "the same claim must refresh, never duplicate"
    refreshed = rows[0]
    assert refreshed.freshness_deadline is not None and old_deadline is not None
    assert refreshed.freshness_deadline > old_deadline
    assert refreshed.retrieved_at is not None
    assert refreshed.freshness_deadline > refreshed.retrieved_at
    # Original provenance is kept: the first observation still cites its source.
    assert refreshed.id == evidence.id

    # Every provider request is recorded (serpapi_docs rule 5).
    runs = await _recheck_runs(db_session, evidence.id)
    assert len(runs) == 1
    assert runs[0].status == RunStatus.SUCCEEDED


async def test_recheck_with_changed_value_adds_row_and_flags_conflict(
    db_session: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A differing value is stored as NEW evidence (old row untouched) and the
    pair is surfaced as a conflict the user must verify."""
    from app.services.evidence.recheck import recheck_evidence

    tag = uuid.uuid4().hex[:8]
    program, evidence = await _seed_program_with_evidence(db_session, tag)
    original_value = dict(evidence.extracted_value or {})

    monkeypatch.setattr(
        "app.services.evidence.recheck.extract_claims",
        _fake_extract({"min": 7.5, "test": "IELTS"}),
    )
    fake = _FakeSerpApi(
        [
            {
                "title": f"M.Sc. Recheck Testing - Recheck University {tag}",
                "link": f"https://recheck-{tag}.example.test/ielts",
                "snippet": "Updated: IELTS 7.5 overall required.",
            }
        ]
    )

    result = await recheck_evidence(
        db_session, evidence.id, serpapi=fake  # type: ignore[arg-type]
    )

    assert result["recheck"]["found"] is True
    new_id = result["recheck"]["new_evidence_id"]
    assert new_id is not None, "a changed value must be stored as new evidence"

    rows = (
        (await db_session.execute(select(Evidence).where(Evidence.subject_id == program.id)))
        .scalars()
        .all()
    )
    assert len(rows) == 2
    by_id = {r.id: r for r in rows}
    # The original row keeps its own value: evidence is never overwritten.
    assert dict(by_id[evidence.id].extracted_value or {}) == original_value
    assert dict(by_id[uuid.UUID(new_id)].extracted_value or {}) == {"min": 7.5, "test": "IELTS"}
    assert {r.status for r in rows} == {EvidenceStatus.CONFLICTING}

    conflicts = (
        (
            await db_session.execute(
                select(EvidenceConflict).where(
                    EvidenceConflict.conflict_key
                    == f"program:{program.id}:ielts_overall_min"
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(conflicts) == 1
    assert conflicts[0].resolution_status == "UNRESOLVED"
    assert conflicts[0].preferred_evidence_id is not None

    # Same run also produces the search_run provenance row.
    assert len(await _recheck_runs(db_session, evidence.id)) == 1


async def test_recheck_without_confirmation_keeps_honest_state(
    db_session: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No fresh confirmation (extraction yields nothing) -> the existing row
    keeps its honest state; nothing is refreshed or invented."""
    from app.services.evidence.recheck import recheck_evidence

    tag = uuid.uuid4().hex[:8]
    _program, evidence = await _seed_program_with_evidence(db_session, tag)

    async def _no_claims(
        provider: Any,
        *,
        title: str | None,
        snippet: str | None,
        domain: str | None,
        extraction_model: str | None = None,
    ) -> None:
        return None

    monkeypatch.setattr("app.services.evidence.recheck.extract_claims", _no_claims)
    fake = _FakeSerpApi(
        [
            {
                "title": "Unrelated result",
                "link": f"https://recheck-{tag}.example.test/other",
                "snippet": "Nothing about language requirements.",
            }
        ]
    )

    result = await recheck_evidence(
        db_session, evidence.id, serpapi=fake  # type: ignore[arg-type]
    )

    assert result["recheck"]["found"] is False
    assert result["recheck"]["refreshed"] is False
    assert result["recheck"]["new_evidence_id"] is None
    assert result["status"] == EvidenceStatus.STALE.value

    rows = (
        (await db_session.execute(select(Evidence).where(Evidence.id == evidence.id)))
        .scalars()
        .all()
    )
    assert len(rows) == 1
    assert rows[0].status == EvidenceStatus.STALE
    # The search itself is still on the books (it did run).
    assert len(await _recheck_runs(db_session, evidence.id)) == 1


async def test_recheck_api_reports_provider_unavailable_without_key(
    db_session: Any, api: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No SERPAPI_API_KEY -> 409 PROVIDER_UNAVAILABLE (no silent fake data)."""
    from app.core.config import Settings

    tag = uuid.uuid4().hex[:8]
    _program, evidence = await _seed_program_with_evidence(db_session, tag)
    monkeypatch.setattr(
        "app.services.evidence.recheck.get_settings",
        lambda: Settings(serpapi_api_key=""),
    )

    response = await api.post(f"/api/v1/evidence/{evidence.id}/recheck")
    assert response.status_code == 409, response.text
    error = response.json()["error"]
    assert error["code"] == "PROVIDER_UNAVAILABLE"
    assert "SERPAPI_API_KEY" in error["message"]


async def test_recheck_api_returns_404_for_unknown_evidence(api: AsyncClient) -> None:
    response = await api.post(f"/api/v1/evidence/{uuid.uuid4()}/recheck")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"
