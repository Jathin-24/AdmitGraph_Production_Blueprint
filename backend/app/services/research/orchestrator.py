"""Research orchestration facade: dispatch entry point + historical import surface.

Was a single ~1400-line module (W7/P2-22 split); the implementation now lives in
the neighbouring package modules and this facade keeps every historical import
working unchanged:

- ``research.runner``    - ResearchService plan lifecycle (create/execute/
  finalize), step runner, plan constants (STRATEGY_VERSION, STEP_ORDER,
  criticality sets) and ``idempotency_hash``
- ``research.search``    - SearchWave/WaveOutcome, planned waves under the
  per-purpose budgets, concurrent provider fetch, search-run persistence,
  discovery and funding waves, locale resolution
- ``research.normalize`` - discovery results -> Program upserts + shortlisting
- ``research.extract``   - bounded extraction window, Evidence/Intake upserts,
  requirement sync and search-result -> program binding

Reliability contract (backend/BACKEND_SPEC.md "Reliability" + serpapi_docs):
- every provider search is bounded by per-purpose budgets (planner.RUN_BUDGETS)
- independent searches run concurrently under asyncio.Semaphore(max_concurrency)
- a Redis-backed cache with in-process fallback caches identical searches
- a run-scoped circuit breaker skips remaining live searches after repeated
  consecutive failures and completes discovery from recent evidence (rule 15)
- step failures classify: critical -> plan FAILED, non-critical -> step PARTIAL
  and the run continues; any PARTIAL step => plan status PARTIAL

``dispatch_plan`` stays here on purpose: callers patch
``orchestrator.ResearchService``/``orchestrator.dispatch_plan`` in tests and
``app.workers.recovery`` re-dispatches through this module attribute.
"""

from __future__ import annotations

import asyncio
import uuid

from app.core import limits
from app.services.research.extract import (
    _BINDABLE_OVERFETCH,
    _EXTRACTION_SHARES,
    PROGRAM_PURPOSES,
    _extraction_quotas,
    _to_confidence,
)
from app.services.research.runner import (
    _STEP_SERVICES,
    CRITICAL_STEPS,
    NON_CRITICAL_STEPS,
    SCORING_VERSION,
    STEP_ORDER,
    STRATEGY_VERSION,
    ResearchService,
    idempotency_hash,
)
from app.services.research.search import (
    MAX_PAGE2_QUERIES,
    PAGE2_START,
    SearchWave,
    WaveOutcome,
)

__all__ = [
    "CRITICAL_STEPS",
    "MAX_PAGE2_QUERIES",
    "NON_CRITICAL_STEPS",
    "PAGE2_START",
    "PROGRAM_PURPOSES",
    "ResearchService",
    "SCORING_VERSION",
    "STEP_ORDER",
    "STRATEGY_VERSION",
    "SearchWave",
    "WaveOutcome",
    "_BINDABLE_OVERFETCH",
    "_EXTRACTION_SHARES",
    "_STEP_SERVICES",
    "_background_tasks",
    "_extraction_quotas",
    "_to_confidence",
    "dispatch_plan",
    "idempotency_hash",
]

_background_tasks: set[asyncio.Task[None]] = set()


def dispatch_plan(plan_id: uuid.UUID) -> None:
    from app.core.runcontext import run_id_scope

    # Bind run_id BEFORE create_task: the Task copies the current context, so
    # every orchestrator log line inside the run carries run_id
    # (BACKEND_SPEC §Reliability "structured logs with request_id and run_id").
    with run_id_scope(str(plan_id)):
        service = ResearchService()
        # Global + per-user concurrency caps (app/core/limits.py, audit P1-7):
        # the slot is taken before execute_plan claims the row, so excess runs
        # wait as QUEUED instead of stampeding SerpApi/LLM/DB. Fails open when
        # the plan's owner cannot be resolved (see limits.run_plan_capped).
        task = asyncio.create_task(limits.run_plan_capped(plan_id, service.execute_plan(plan_id)))
        _background_tasks.add(task)
        task.add_done_callback(_background_tasks.discard)

        def _finished(t: asyncio.Task[None]) -> None:
            from app.core.events import emit  # local: keeps orchestrator import-light

            emit("research.run_finished", plan_id=plan_id, task=t)

        task.add_done_callback(_finished)
