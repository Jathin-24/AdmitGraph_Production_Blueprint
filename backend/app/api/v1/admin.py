"""Admin/observability endpoints. Never expose provider credentials."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import ResearchPlan, ResearchPlanStep, RunStatus, SearchRun
from app.db.session import get_session

router = APIRouter(tags=["admin"])


@router.get("/admin/search-usage")
async def search_usage(session: AsyncSession = Depends(get_session)) -> dict[str, Any]:
    """Aggregated SerpApi usage. Contains no keys, URLs-with-tokens or payloads."""
    total = (
        await session.execute(select(func.count()).select_from(SearchRun))
    ).scalar_one()
    by_engine_rows = (
        await session.execute(
            select(SearchRun.engine, func.count()).group_by(SearchRun.engine).order_by(SearchRun.engine)
        )
    ).all()
    by_status_rows = (
        await session.execute(
            select(SearchRun.status, func.count()).group_by(SearchRun.status).order_by(SearchRun.status)
        )
    ).all()
    agg = (
        await session.execute(
            select(
                func.count(SearchRun.id),
                func.coalesce(func.sum(SearchRun.result_count), 0),
                func.coalesce(func.sum(SearchRun.duration_ms), 0),
                func.coalesce(func.count(SearchRun.id).filter(SearchRun.cache_hit.is_(True)), 0),
            )
        )
    ).one()
    searches, results, duration_ms, cache_hits = agg
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
    total = (await session.execute(select(func.count()).select_from(ResearchPlan))).scalar_one()
    plans = (
        await session.execute(
            select(ResearchPlan)
            .order_by(ResearchPlan.created_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    ).scalars().all()
    items: list[dict[str, Any]] = []
    for plan in plans:
        steps = (
            await session.execute(
                select(ResearchPlanStep).where(ResearchPlanStep.research_plan_id == plan.id)
            )
        ).scalars().all()
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
