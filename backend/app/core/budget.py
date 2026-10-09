"""Daily search spend budget (audit P1-8 / PLAN "P1-8 daily search/LLM budget
with 429 BUDGET_EXCEEDED").

The per-IP rate limits (app.main) and the P1-7 concurrency caps
(app.core.limits) bound a single request and a single burst, but nothing
bounded the AGGREGATE: a script replaying POST /research/runs all day could
spend SerpApi/LLM budget without limit. This module counts the rows in
``search_runs`` created today (UTC) and rejects new runs once the configured
budget is spent.

Semantics
---------
* ``AGRAPH_DAILY_SEARCH_BUDGET`` — global cap of ``search_runs`` rows per
  UTC day (default 500; ``0`` = unlimited). Enforced with a single COUNT.
* ``AGRAPH_DAILY_SEARCH_BUDGET_PER_USER`` — per-user cap over the same rows
  (default ``0`` = unlimited); the table attributes rows through its nullable
  ``user_id`` column, so this cap is enforced too.

Both caps answer ``429`` with error code ``BUDGET_EXCEEDED`` and a
plain-language message BEFORE any plan row is created or dispatched — a
blocked request writes nothing (same fail-fast contract as P1-7).

Implementation notes
--------------------
* One cheap ``SELECT count(*)`` per check, filtered by
  ``requested_at >= <utc midnight>``. The per-user filter is index-friendly
  through the existing ``idx_search_runs_user (user_id, requested_at)``; the
  global filter has no ``requested_at``-leading index, so it is a sequential
  scan — accepted on purpose (no migration: Alembic is owned by another
  workstream; add an index on ``requested_at`` later if the table grows large
  enough for the count to matter).
* The count is advisory: two truly-concurrent creations can both pass the
  check and overshoot by a run or two. That is the trade-off every
  non-locking budget makes — the cap's job is bounding daily spend, not
  transactional exactness.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.errors import AppError
from app.db.models import SearchRun

BUDGET_EXCEEDED_CODE = "BUDGET_EXCEEDED"
BUDGET_EXCEEDED_MESSAGE = "Daily research budget reached — try again tomorrow"
PER_USER_BUDGET_EXCEEDED_MESSAGE = (
    "You've used up today's research allowance for your account — try again tomorrow."
)


def utc_day_start(now: datetime | None = None) -> datetime:
    """Midnight UTC of the day ``now`` (default: right now) falls on."""
    if now is None:
        now = datetime.now(UTC)
    return datetime(now.year, now.month, now.day, tzinfo=UTC)


async def searches_today(session: AsyncSession, *, user_id: uuid.UUID | None = None) -> int:
    """``search_runs`` rows created since midnight UTC (all users, or one)."""
    stmt = (
        select(func.count())
        .select_from(SearchRun)
        .where(SearchRun.requested_at >= utc_day_start())
    )
    if user_id is not None:
        stmt = stmt.where(SearchRun.user_id == user_id)
    return int((await session.execute(stmt)).scalar_one())


async def reject_if_budget_spent(session: AsyncSession, user_id: uuid.UUID) -> None:
    """Raise ``429 BUDGET_EXCEEDED`` when today's spend is at/over a non-zero budget.

    ``0`` budgets are unlimited and skip the COUNT entirely.
    """
    settings = get_settings()
    if settings.daily_search_budget > 0:
        if await searches_today(session) >= settings.daily_search_budget:
            raise AppError(429, BUDGET_EXCEEDED_CODE, BUDGET_EXCEEDED_MESSAGE)
    if settings.daily_search_budget_per_user > 0:
        if await searches_today(session, user_id=user_id) >= settings.daily_search_budget_per_user:
            raise AppError(429, BUDGET_EXCEEDED_CODE, PER_USER_BUDGET_EXCEEDED_MESSAGE)
