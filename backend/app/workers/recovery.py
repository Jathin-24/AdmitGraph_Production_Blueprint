"""Idempotent startup recovery for research runs interrupted by a restart.

MASTER_SPEC §19 (Non-functional requirements): "Idempotent research runs."

At process start (FastAPI lifespan, main.py):

- plans stuck QUEUED for longer than QUEUED_STALE_SECONDS never got a worker
  (or the process died before dispatch): they are claimed and re-dispatched —
  live plans through ``orchestrator.dispatch_plan``, demo plans through the
  demo runner's dispatch so the demo replay stays zero-spend;
- plans RUNNING for longer than RUNNING_STALE_SECONDS cannot still be making
  progress: they are failed honestly with ``INTERRUPTED_MESSAGE``.

Idempotency: a stale QUEUED plan is flipped to RUNNING *before* it is handed
to a dispatch function, so a second recovery pass (or a concurrent dispatch)
cannot start the same plan twice; RUNNING claims are terminal (FAILED), so
they are never revisited either. The function only reports counts, so it is
safe to call repeatedly.

Replica safety (audit P1-7): the whole pass runs under a PostgreSQL advisory
lock (`app.workers.locks.RECOVERY_LOCK`); a booting replica that loses the
race returns (0, 0) immediately instead of recovering the same rows twice.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.config import get_settings
from app.db.models import ResearchPlan, RunStatus
from app.workers.locks import RECOVERY_LOCK, try_advisory_lock

log = logging.getLogger(__name__)

# Timeout constants (module-level so tests and operators can reference them).
QUEUED_STALE_SECONDS = 120  # a QUEUED plan older than this has lost its worker
RUNNING_STALE_SECONDS = 60 * 60  # no run may sit RUNNING for over an hour
INTERRUPTED_MESSAGE = "interrupted by restart"


async def recover_research_plans() -> tuple[int, int]:
    """Re-dispatch stale QUEUED plans and fail long-dead RUNNING plans.

    Returns ``(re-dispatched, failed)`` counts for logging/tests. Uses its own
    short-lived engine so recovery never leaves connections pooled on the
    caller's event loop, and never raises on an unreachable database when
    called from the lifespan (the caller guards the call).
    """
    now = datetime.now(UTC)
    queued_cutoff = now - timedelta(seconds=QUEUED_STALE_SECONDS)
    running_cutoff = now - timedelta(seconds=RUNNING_STALE_SECONDS)

    engine = create_async_engine(get_settings().database_url, pool_pre_ping=True)
    try:
        # Only one replica recovers at a time (audit P1-7): whoever loses the
        # advisory-lock race skips — the winner performs identical claims.
        async with try_advisory_lock(engine, RECOVERY_LOCK) as acquired:
            if not acquired:
                log.info(
                    "research startup recovery skipped: another replica holds the recovery lock"
                )
                return (0, 0)
            maker = async_sessionmaker(engine, expire_on_commit=False)
            claimed: list[tuple[uuid.UUID, str]] = []
            failed = 0
            async with maker() as session:
                stale_queued = list(
                    (
                        await session.execute(
                            select(ResearchPlan).where(
                                ResearchPlan.status == RunStatus.QUEUED,
                                ResearchPlan.created_at < queued_cutoff,
                            )
                        )
                    )
                    .scalars()
                    .all()
                )
                stale_running = list(
                    (
                        await session.execute(
                            select(ResearchPlan).where(
                                ResearchPlan.status == RunStatus.RUNNING,
                                or_(
                                    ResearchPlan.started_at < running_cutoff,
                                    and_(
                                        ResearchPlan.started_at.is_(None),
                                        ResearchPlan.created_at < running_cutoff,
                                    ),
                                ),
                            )
                        )
                    )
                    .scalars()
                    .all()
                )
                for plan in stale_running:
                    plan.status = RunStatus.FAILED
                    plan.error_message = INTERRUPTED_MESSAGE
                    plan.completed_at = now
                    failed += 1
                for plan in stale_queued:
                    # Claim before dispatching: a second pass sees RUNNING, not
                    # QUEUED, so the plan can never be dispatched twice.
                    plan.status = RunStatus.RUNNING
                    plan.started_at = now
                    claimed.append((plan.id, plan.mode))
                if stale_queued or stale_running:
                    await session.commit()

            requeued = 0
            for plan_id, mode in claimed:
                if mode == "demo":
                    # Demo plans replay the captured run (zero SerpApi/LLM spend);
                    # re-dispatching them through the live orchestrator would not.
                    from app.services.demo import runner as demo_runner

                    demo_runner.dispatch_demo_plan(plan_id)
                else:
                    from app.services.research import orchestrator

                    orchestrator.dispatch_plan(plan_id)
                requeued += 1

            if requeued or failed:
                log.info(
                    "research startup recovery: re-dispatched %d stale QUEUED run(s), "
                    "failed %d interrupted RUNNING run(s)",
                    requeued,
                    failed,
                )
            else:
                log.info("research startup recovery: nothing to recover")
            return requeued, failed
    finally:
        await engine.dispose()
