"""Concurrency caps for research runs (audit P1-7, PLAN "P1-7 advisory lock
... + global/per-user run concurrency caps").

Two limits, both enforced as WAITING gates (a run queues instead of failing):

* global cap — at most ``AGRAPH_MAX_GLOBAL_RUNS`` (default 4) research runs
  execute at once across the process, protecting SerpApi/LLM budget and the
  database from a stampede;
* per-user cap — at most ``AGRAPH_MAX_RUNS_PER_USER`` (default 2) concurrent
  runs per student, so one student's backlog can never starve everyone else.

Implementation notes
--------------------
State is two plain counters guarded by a single ``asyncio.Condition``: every
waiter re-evaluates the predicate on wake-up, so there is exactly one source
of truth (no per-user primitives to leak or evict, no hold-and-wait across
multiple locks, hence no deadlock). Idle users drop out of the map on release
so it stays bounded by users with runs in flight.

Enforcement point: ``run_plan_capped`` wraps the coroutine created at the
dispatch entry in ``orchestrator.dispatch_plan`` — one import plus one line
there, no restructuring of the orchestrator. Resolving the plan's owner fails
OPEN (returns no cap): a run must never be blocked by a lookup error, and
``execute_plan`` fails honestly on its own if the database is unreachable.
"""

from __future__ import annotations

import asyncio
import logging
import os
import uuid
from collections.abc import AsyncIterator, Coroutine
from contextlib import asynccontextmanager
from typing import Any

log = logging.getLogger(__name__)

DEFAULT_MAX_GLOBAL_RUNS = 4
DEFAULT_MAX_RUNS_PER_USER = 2

ENV_MAX_GLOBAL_RUNS = "AGRAPH_MAX_GLOBAL_RUNS"
ENV_MAX_RUNS_PER_USER = "AGRAPH_MAX_RUNS_PER_USER"


def _env_positive_int(name: str, default: int) -> int:
    """Read a positive int from the environment; bad values fall back."""
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        log.warning("ignoring non-integer %s=%r (using %d)", name, raw, default)
        return default
    if value < 1:
        log.warning("ignoring %s=%d (must be >= 1; using %d)", name, value, default)
        return default
    return value


class RunLimiter:
    """Async gate for concurrent research runs (global + per-user caps)."""

    def __init__(
        self,
        max_global: int = DEFAULT_MAX_GLOBAL_RUNS,
        max_per_user: int = DEFAULT_MAX_RUNS_PER_USER,
    ) -> None:
        if max_global < 1 or max_per_user < 1:
            raise ValueError("run limits must be >= 1")
        self.max_global = max_global
        self.max_per_user = max_per_user
        self._cond = asyncio.Condition()
        self._active_global = 0
        self._active_per_user: dict[uuid.UUID, int] = {}

    @property
    def active_global(self) -> int:
        """Runs currently holding a slot (the /metrics gauge reads this)."""
        return self._active_global

    def active_for(self, user_id: uuid.UUID) -> int:
        return self._active_per_user.get(user_id, 0)

    async def acquire(self, user_id: uuid.UUID) -> None:
        """Wait until both caps admit this user's next run, then take a slot."""
        async with self._cond:
            while (
                self._active_global >= self.max_global
                or self._active_per_user.get(user_id, 0) >= self.max_per_user
            ):
                await self._cond.wait()
            self._active_global += 1
            self._active_per_user[user_id] = self._active_per_user.get(user_id, 0) + 1

    async def release(self, user_id: uuid.UUID) -> None:
        """Give a slot back and wake everyone waiting on either cap."""
        async with self._cond:
            if self._active_global > 0:
                self._active_global -= 1
            remaining = self._active_per_user.get(user_id, 0) - 1
            if remaining > 0:
                self._active_per_user[user_id] = remaining
            else:
                # Drop idle users entirely: the map only holds users with
                # runs in flight (safe — waiters re-read the counter).
                self._active_per_user.pop(user_id, None)
            self._cond.notify_all()

    @asynccontextmanager
    async def slot(self, user_id: uuid.UUID) -> AsyncIterator[None]:
        """Hold one (global + per-user) slot for the duration of the block."""
        await self.acquire(user_id)
        try:
            yield
        finally:
            await self.release(user_id)


_limiter: RunLimiter | None = None


def get_limiter() -> RunLimiter:
    """Process-wide limiter, configured once from the environment."""
    global _limiter
    if _limiter is None:
        _limiter = RunLimiter(
            max_global=_env_positive_int(ENV_MAX_GLOBAL_RUNS, DEFAULT_MAX_GLOBAL_RUNS),
            max_per_user=_env_positive_int(ENV_MAX_RUNS_PER_USER, DEFAULT_MAX_RUNS_PER_USER),
        )
    return _limiter


def reset_limiter() -> None:
    """Drop the singleton (tests / after env changes)."""
    global _limiter
    _limiter = None


@asynccontextmanager
async def run_slot(user_id: uuid.UUID) -> AsyncIterator[None]:
    """Module-level convenience wrapper around the process-wide limiter."""
    async with get_limiter().slot(user_id):
        yield


async def _plan_owner_id(plan_id: uuid.UUID) -> uuid.UUID | None:
    """User behind a research plan; None when it cannot be resolved (fail-open)."""
    try:
        from sqlalchemy import select
        from sqlalchemy.ext.asyncio import async_sessionmaker

        from app.db.models import ResearchPlan, StudentProfile  # local: keep import light
        from app.db.session import get_engine

        maker = async_sessionmaker(get_engine(), expire_on_commit=False)
        async with maker() as session:
            result = await session.execute(
                select(StudentProfile.user_id)
                .join(ResearchPlan, ResearchPlan.profile_id == StudentProfile.id)
                .where(ResearchPlan.id == plan_id)
            )
            return result.scalar_one_or_none()
    except Exception:  # noqa: BLE001 - a lookup failure must never block a run
        log.debug("run cap: owner lookup failed for plan %s", plan_id, exc_info=True)
        return None


async def run_plan_capped(plan_id: uuid.UUID, coro: Coroutine[Any, Any, None]) -> None:
    """Await ``coro`` under the concurrency caps for the plan's owner.

    Wraps ``service.execute_plan(plan_id)`` at the dispatch entry. If the
    owner cannot be resolved the run proceeds uncapped (fail-open).
    """
    owner = await _plan_owner_id(plan_id)
    try:
        if owner is None:
            await coro
        else:
            async with run_slot(owner):
                await coro
    finally:
        # No-op once awaited; releases a never-started coroutine when the
        # task was cancelled while waiting for a slot.
        coro.close()
