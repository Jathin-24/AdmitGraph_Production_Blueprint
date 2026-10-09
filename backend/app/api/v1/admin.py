"""Admin/observability endpoints. Never expose provider credentials.

All routes are gated by `require_admin_access` (router-level dependency):
the role must come from the verified bearer token (auth middleware decodes
it once per request). Anonymous callers are ALWAYS 403 FORBIDDEN — the demo
account's stored role is deliberately not consulted, because a demo user
seeded ADMIN used to hand anonymous visitors the admin stats.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError
from app.core.security import current_user_role
from app.db.models import RunStatus
from app.db.repositories import admin as admin_repo
from app.db.session import get_session


def require_admin_access() -> None:
    """403 unless the caller is an authenticated ADMIN (see module docstring)."""
    role = current_user_role()
    if role != "ADMIN":
        # role is None for anonymous callers (no bearer token): always 403
        # FORBIDDEN — never a demo-role fallback. Invalid tokens never reach
        # here: the auth middleware rejects them earlier with 401.
        raise AppError(403, "FORBIDDEN", "Administrator access required")


router = APIRouter(tags=["admin"], dependencies=[Depends(require_admin_access)])


@router.get("/admin/search-usage")
async def search_usage(session: AsyncSession = Depends(get_session)) -> dict[str, Any]:
    """Aggregated SerpApi usage. Contains no keys, URLs-with-tokens or payloads."""
    total = await admin_repo.count_search_runs(session)
    by_engine_rows = await admin_repo.search_runs_by_engine(session)
    by_status_rows = await admin_repo.search_runs_by_status(session)
    searches, results, duration_ms, cache_hits = await admin_repo.search_usage_totals(session)
    searches = int(searches or 0)
    results = int(results or 0)
    duration_ms = int(duration_ms or 0)
    return {
        "total_searches": total,
        "successful_searches": searches,
        "total_results": results,
        "cache_hits": cache_hits,
        "avg_duration_ms": round(duration_ms / searches, 1) if searches else None,
        "by_engine": {engine: count for engine, count in by_engine_rows},
        "by_status": {
            status.value if hasattr(status, "value") else str(status): count
            for status, count in by_status_rows
        },
    }


@router.get("/admin/research-runs")
async def research_runs(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    total = await admin_repo.count_research_plans(session)
    plans = await admin_repo.list_research_plans(session, (page - 1) * page_size, page_size)
    items: list[dict[str, Any]] = []
    for plan in plans:
        steps = await admin_repo.list_plan_steps(session, plan.id)
        items.append(
            {
                "id": str(plan.id),
                "status": plan.status.value,
                "created_at": plan.created_at.isoformat(),
                "completed_at": plan.completed_at.isoformat() if plan.completed_at else None,
                "error_message": plan.error_message,
                "steps_total": len(steps),
                "steps_failed": sum(1 for s in steps if s.status == RunStatus.FAILED),
                "steps_partial": sum(1 for s in steps if s.status == RunStatus.PARTIAL),
            }
        )
    has_more = page * page_size < total
    return {
        "items": items,
        "page": page,
        "page_size": page_size,
        "total": total,
        "next_cursor": str(page + 1) if has_more else None,
    }
