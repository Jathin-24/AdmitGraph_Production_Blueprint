"""Funding service: scholarship query planning + scholarship evidence surfacing.

Scholarship claims have no `requirement_type` home in the requirements model
(frontend categories: academic/prerequisite/language/test/document/deadline/
tuition/budget/policy), so they are kept as evidence rows (claim_type
"scholarship") and surfaced through this service's output instead of inventing
schema. Funding searches are bounded by MAX_FUNDING_QUERIES per run.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Evidence, Source
from app.services.research.planner import (
    MAX_FUNDING_QUERIES,
    PlannedQuery,
    plan_scholarship_query,
)


def scholarship_query_for(
    program_name: str,
    official_domain: str | None,
    country_name: str,
    field_name: str,
    *,
    budget_remaining: int,
) -> PlannedQuery | None:
    """Site-restricted scholarship query when the run still has funding budget.

    Returns None when the funding budget is exhausted (application-level
    safeguard, serpapi_docs "API usage budget").
    """
    if budget_remaining <= 0:
        return None
    return plan_scholarship_query(program_name, official_domain, country_name, field_name)


async def scholarship_summary(
    session: AsyncSession, program_ids: list[UUID]
) -> dict[str, Any]:
    """Scholarship evidence for the given programs (bounded, deterministic).

    Absence of claims is a valid outcome: `scholarship_claims == 0` means
    UNKNOWN, never "no scholarships exist".
    """
    if not program_ids:
        return {"scholarship_claims": 0, "items": []}
    rows = (
        await session.execute(
            select(Evidence, Source)
            .join(Source, Evidence.source_id == Source.id, isouter=True)
            .where(
                Evidence.claim_type == "scholarship",
                Evidence.subject_type == "program",
                Evidence.subject_id.in_(program_ids),
            )
            .order_by(Evidence.retrieved_at.desc())
            .limit(20)
        )
    ).all()
    return {
        "scholarship_claims": len(rows),
        "items": [
            {
                "id": str(e.id),
                "program_id": str(e.subject_id) if e.subject_id else None,
                "claim": e.claim,
                "confidence": e.confidence.value,
                "status": e.status.value,
                "source_domain": s.domain if s else None,
                "source_url": s.url if s else None,
            }
            for e, s in rows
        ],
        "budget_per_run": MAX_FUNDING_QUERIES,
    }
