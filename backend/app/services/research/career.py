"""Career signal service: google_jobs query planning + result summarizing.

Job-market signal comes from the google_jobs engine (serpapi_docs "Career"),
never from treating general web results as job-market data. Queries are bounded
by MAX_CAREER_QUERIES per run; planning lives in planner.plan_queries.

`record_career_evidence` turns STORED google_jobs results into evidence rows
(claim_type "career") with full provenance — no live search is ever issued
here. The rows are evidence-only: `career_market_signal` is not in
llm_extract.KNOWN_CLAIM_KEYS, so requirements sync never sees them (the
evaluator has no requirement home for a job-market signal). Subject binding is
conservative: a program id is attached only through a safe, unambiguous rule;
otherwise the row stays unbound (subject_id NULL) rather than guessing.

Wiring (research orchestrator discovery step, next to the summary):

    "career": {
        **career_summary(results),
        **await record_career_evidence(session, search_run_ids=run_ids),
    }

Provider failure / zero results: this function only reads rows that already
exist, so an errored or skipped run simply yields honest zeros.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import ConfidenceLevel, SearchResult, SearchRun, Source
from app.services.evidence.extraction import (
    EvidenceExtractionService,
    ExtractedClaim,
    load_signal_rows,
    safe_program_subject,
)
from app.services.research.planner import MAX_CAREER_QUERIES
from app.services.research.source_profiles import is_official_source

# Evidence-only claim key (documented deferral in llm_extract.KNOWN_CLAIM_KEYS).
CAREER_CLAIM_KEY = "career_market_signal"

# Bound per recording pass: <= MAX_CAREER_QUERIES queries/run * ~10 stored
# results each, read newest-first. Recording is idempotent (record_claims
# refreshes re-observed rows), so repeated passes never grow the table.
MAX_CAREER_EVIDENCE_RESULTS = 20


def career_summary(search_results: list[dict[str, Any]]) -> dict[str, Any]:
    """Summarize stored google_jobs results for a research run's step output.

    `jobs_found` counts stored job result items; zero results means UNKNOWN
    (no job-market claim is made without evidence).
    """
    jobs = [r for r in search_results if r.get("engine") == "google_jobs"]
    jobs_found = sum(int(r.get("count") or 0) for r in jobs)
    return {
        "career_queries_run": len(jobs),
        "career_queries_budget": MAX_CAREER_QUERIES,
        "jobs_found": jobs_found,
        "engine": "google_jobs",
    }


def _career_claim(
    result: SearchResult,
    run: SearchRun,
    source: Source,
    program_id: UUID | None,
) -> ExtractedClaim:
    """One evidence-only career claim grounded in a stored job result."""
    title = (result.title or "").strip() or "(untitled google_jobs result)"
    query = str(run.query or "")
    value: dict[str, Any] = {"query": query, "engine": "google_jobs"}
    if result.position is not None:
        value["position"] = result.position
    if run.result_count is not None:
        value["results_in_run"] = run.result_count
    return ExtractedClaim(
        claim_type="career",
        normalized_key=CAREER_CLAIM_KEY,
        value=value,
        claim=f"Stored google_jobs result '{title}' for query '{query}'.",
        subject_type="program",
        subject_id=program_id,
        confidence=ConfidenceLevel.HIGH if is_official_source(source) else ConfidenceLevel.MEDIUM,
    )


async def record_career_evidence(
    session: AsyncSession,
    *,
    search_run_ids: list[UUID] | None = None,
    limit: int = MAX_CAREER_EVIDENCE_RESULTS,
) -> dict[str, int]:
    """Record evidence rows from stored google_jobs results (stored rows only).

    Subject binding uses only safe rules (exact normalized program title or a
    unique institution-domain match); rows that cannot bind are recorded
    unbound so the signal stays auditable. Confidence is HIGH for
    official-domain sources and MEDIUM otherwise. Freshness follows
    services/evidence/freshness.py (`career` = 14 days).
    """
    rows = await load_signal_rows(
        session,
        purposes=("career",),
        engines=("google_jobs",),
        search_run_ids=search_run_ids,
        limit=limit,
    )
    recorded = 0
    bound = 0
    service = EvidenceExtractionService()
    for result, source, run in rows:
        # Safe binding only; when no rule matches the subject stays NULL.
        program_id = await safe_program_subject(session, title=result.title, source=source)
        stored = await service.record_claims(
            session, source, result, [_career_claim(result, run, source, program_id)]
        )
        recorded += len(stored)
        bound += sum(1 for row in stored if row.subject_id is not None)
    if recorded:
        await session.commit()
    return {
        "career_results_considered": len(rows),
        "career_evidence_recorded": recorded,
        "career_evidence_bound": bound,
    }


__all__ = [
    "CAREER_CLAIM_KEY",
    "MAX_CAREER_EVIDENCE_RESULTS",
    "career_summary",
    "record_career_evidence",
]
