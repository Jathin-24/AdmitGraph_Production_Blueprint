"""Scholarship finder endpoints (W13).

The router is pre-registered in app/main.py — fill in routes here; do not
edit main.py. Reference data: scholarships are served from a verified JSON
dataset (no table, no migration). Filters: q, country, degree_level,
funding_type — mirror the programs endpoint's query style.

``q`` is a case-insensitive substring over name / provider / eligibility;
``country`` is a 2-letter ISO code (``null`` rows are multi-country schemes
and match no country filter); ``degree_level`` is one of bachelors | masters |
phd; ``funding_type`` is one of full | partial | merit | need-based. Values
this build does not recognise simply match nothing (same behaviour as the
programs endpoint's ``degree_level``). The response envelope is identical to
``GET /programs``: {items, page, page_size, total, next_cursor}.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query

from app.services import scholarships as scholarships_service

router = APIRouter(tags=["scholarships"])


@router.get("/scholarships")
async def list_scholarships(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=scholarships_service.MAX_PAGE_SIZE),
    q: str = Query("", max_length=200),
    country: str | None = Query(None, max_length=8),
    degree_level: str | None = Query(None, max_length=16),
    funding_type: str | None = Query(None, max_length=16),
) -> dict[str, Any]:
    """Browse the verified scholarship dataset (PLAN.md W13 contract).

    Pure reference data — no session, no profile, no database. Every item
    carries a real ``source_url`` and the dataset's ``last_checked`` date so
    the UI can tell a student when the figures were last seen at the source.
    """
    return scholarships_service.list_scholarships(
        q=q,
        country=country,
        degree_level=degree_level,
        funding_type=funding_type,
        page=page,
        page_size=page_size,
    )
