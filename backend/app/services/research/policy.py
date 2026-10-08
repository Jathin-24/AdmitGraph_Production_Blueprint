"""Policy signal service: country visa/financial-proof query planning + summary.

Query templates are routing configuration (which official page to ask for), not
data claims: no visa rule, deadline or financial-proof amount is asserted here.
Bounded by MAX_POLICY_QUERIES per run; planning lives in planner.plan_queries.

`record_policy_evidence` turns STORED policy search results into evidence rows
(claim_type "policy") with full provenance — no live search is ever issued
here. The rows are evidence-only: `policy_intake_signal` is not in
llm_extract.KNOWN_CLAIM_KEYS, so requirements sync never sees them (there is no
requirement home for a policy signal). Subject binding is conservative: a
program id is attached only through a safe, unambiguous rule; otherwise the row
stays unbound (subject_id NULL) rather than guessing.

Wiring (research orchestrator discovery step, next to the summary):

    "policy": {
        **policy_summary(results),
        **await record_policy_evidence(session, search_run_ids=run_ids),
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
from app.services.research.planner import MAX_POLICY_QUERIES
from app.services.research.source_profiles import is_official_source

# Evidence-only claim key (documented deferral in llm_extract.KNOWN_CLAIM_KEYS).
POLICY_CLAIM_KEY = "policy_intake_signal"

# Bound per recording pass (<= MAX_POLICY_QUERIES queries/run), newest-first.
MAX_POLICY_EVIDENCE_RESULTS = 20


def policy_summary(search_results: list[dict[str, Any]]) -> dict[str, Any]:
    """Summarize stored policy searches for a research run's step output."""
    policy_runs = [r for r in search_results if r.get("purpose") == "policy"]
    return {
        "policy_queries_run": len(policy_runs),
        "policy_queries_budget": MAX_POLICY_QUERIES,
        "queries": [str(r.get("query", "")) for r in policy_runs],
    }


def _policy_claim(
    result: SearchResult,
    run: SearchRun,
    source: Source,
    program_id: UUID | None,
) -> ExtractedClaim:
    """One evidence-only policy claim grounded in a stored search result."""
    title = (result.title or "").strip() or "(untitled policy result)"
    query = str(run.query or "")
    value: dict[str, Any] = {"query": query, "engine": str(run.engine or "google")}
    if result.position is not None:
        value["position"] = result.position
    if run.result_count is not None:
        value["results_in_run"] = run.result_count
    return ExtractedClaim(
        claim_type="policy",
        normalized_key=POLICY_CLAIM_KEY,
        value=value,
        claim=f"Stored policy search result '{title}' for query '{query}'.",
        subject_type="program",
        subject_id=program_id,
        confidence=ConfidenceLevel.HIGH if is_official_source(source) else ConfidenceLevel.MEDIUM,
    )


async def record_policy_evidence(
    session: AsyncSession,
    *,
    search_run_ids: list[UUID] | None = None,
    limit: int = MAX_POLICY_EVIDENCE_RESULTS,
) -> dict[str, int]:
    """Record evidence rows from stored policy searches (stored rows only).

    Subject binding uses only safe rules (exact normalized program title or a
    unique institution-domain match); rows that cannot bind are recorded
    unbound so the signal stays auditable. Confidence is HIGH for
    official-domain sources (government/study portals) and MEDIUM otherwise.
    Freshness follows services/evidence/freshness.py: `policy` has no window of
    its own, so the documented 30-day default applies.
    """
    rows = await load_signal_rows(
        session,
        purposes=("policy",),
        engines=(),
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
            session, source, result, [_policy_claim(result, run, source, program_id)]
        )
        recorded += len(stored)
        bound += sum(1 for row in stored if row.subject_id is not None)
    if recorded:
        await session.commit()
    return {
        "policy_results_considered": len(rows),
        "policy_evidence_recorded": recorded,
        "policy_evidence_bound": bound,
    }


__all__ = [
    "MAX_POLICY_EVIDENCE_RESULTS",
    "POLICY_CLAIM_KEY",
    "policy_summary",
    "record_policy_evidence",
]
