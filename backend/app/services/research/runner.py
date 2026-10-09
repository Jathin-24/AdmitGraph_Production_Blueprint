"""
ResearchService: plan lifecycle and step execution (W7 split from research.orchestrator).

The former ~1400-line orchestrator god module (P2-22) keeps its plan
creation/finalization and step runner here; wave/search work lives in
research.search, program normalization in research.normalize and claim
extraction in research.extract. Delegating methods below keep the
historical `service._x(...)` surface for tests and internal callers.
"""

from __future__ import annotations

import hashlib
import json
import logging
import uuid
from collections import defaultdict
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings, get_settings
from app.db.models import (
    Program,
    ResearchPlan,
    ResearchPlanStep,
    RunStatus,
    SearchResult,
    Source,
)
from app.db.session import get_engine
from app.services.research.extract import (
    extract_evidence,
    match_program,
    select_extraction_results,
    upsert_intakes_from_evidence,
)
from app.services.research.normalize import normalize_programs, shortlist_programs
from app.services.research.planner import (
    RUN_BUDGETS,
    plan_queries,
)
from app.services.research.search import (
    SearchWave,
    WaveOutcome,
    fetch_waves,
    planned_waves,
    profile_countries,
    recent_evidence_fallback,
    record_outcomes,
    result_stats,
    run_discovery,
    run_funding_queries,
    run_program_queries,
    search_locale_for,
    upsert_source,
)
from app.services.serpapi.cache import SearchCache
from app.services.serpapi.circuit import CircuitBreaker
from app.services.serpapi.client import SerpApiClient, SerpApiError

logger = logging.getLogger(__name__)


STRATEGY_VERSION = "v1"
SCORING_VERSION = "v1"

# Step criticality (BACKEND_SPEC "graceful partial strategy"):
# critical failure  -> plan FAILED (nothing trustworthy can continue)
# non-critical fail -> step PARTIAL, plan continues; plan ends PARTIAL.
# A missing/unclassified step is treated as non-critical (partial, not fatal).
CRITICAL_STEPS = frozenset(
    {"validate_profile", "plan_queries", "evaluate_requirements", "score_fit", "build_strategy"}
)
NON_CRITICAL_STEPS = frozenset(
    {
        "discovery_search",
        "normalize_programs",
        "extract_evidence",
        "assess_risks",
        "career_check",
        "news_check",
        "funding_check",
        "policy_check",
    }
)

STEP_ORDER = [
    "validate_profile",
    "plan_queries",
    "discovery_search",
    "normalize_programs",
    "extract_evidence",
    "evaluate_requirements",
    "score_fit",
    "assess_risks",
    "build_strategy",
]


def idempotency_hash(profile_id: uuid.UUID, goal: dict[str, Any]) -> str:
    stable = json.dumps(
        {
            "profile_id": str(profile_id),
            "goal": goal,
            "strategy_version": STRATEGY_VERSION,
        },
        sort_keys=True,
        default=str,
    )
    return hashlib.sha256(stable.encode()).hexdigest()


class ResearchService:
    def __init__(
        self,
        session: AsyncSession | None = None,
        settings: Settings | None = None,
        serpapi: SerpApiClient | None = None,
    ) -> None:
        self._session = session
        self._settings = settings or get_settings()
        self._serpapi = serpapi or SerpApiClient(self._settings, cache=SearchCache(self._settings))
        self._circuit = CircuitBreaker(threshold=self._settings.serpapi_circuit_threshold)
        # SerpApi localization (hl/gl/location/google_domain) for the active
        # profile's target country; set per step in _execute_step.
        self._search_locale: dict[str, Any] | None = None

    # ------------------------------------------------------------------ plan
    async def create_plan(
        self,
        session: AsyncSession,
        profile_id: uuid.UUID,
        goal: dict[str, Any],
        idempotency_key: str | None = None,
    ) -> ResearchPlan:
        if idempotency_key:
            digest = hashlib.sha256((idempotency_key + str(profile_id)).encode()).hexdigest()
            existing = await session.execute(
                select(ResearchPlan).where(
                    ResearchPlan.profile_id == profile_id,
                    ResearchPlan.requested_goal["_idempotency"].astext == digest,
                )
            )
            found = existing.scalar_one_or_none()
            if found is not None and found.status in (RunStatus.QUEUED, RunStatus.RUNNING):
                return found
            goal = {**goal, "_idempotency": digest}

        plan = ResearchPlan(profile_id=profile_id, requested_goal=goal, status=RunStatus.QUEUED)
        session.add(plan)
        await session.flush()
        for key, service in ((k, s) for k, s in _STEP_SERVICES()):
            session.add(ResearchPlanStep(research_plan_id=plan.id, step_key=key, service_name=service))
        await session.commit()
        await session.refresh(plan)
        return plan

    async def execute_plan(self, plan_id: uuid.UUID) -> None:
        """Runs the plan end-to-end; safe for background execution. Each step commits."""
        get_engine()
        maker = async_sessionmaker(get_engine(), expire_on_commit=False)
        # Fresh circuit per run: the breaker counts this run's searches only.
        self._circuit = CircuitBreaker(threshold=self._settings.serpapi_circuit_threshold)
        async with maker() as session:
            plan = await session.get(ResearchPlan, plan_id)
            if plan is None:
                return
            plan.status = RunStatus.RUNNING
            plan.started_at = datetime.now(UTC)
            await session.commit()
            critical_failed: list[str] = []
            try:
                await self._run_steps(session, plan, critical_failed)
            except Exception as exc:  # noqa: BLE001 - orchestrator must not crash worker
                logger.exception("research plan %s failed", plan_id)
                plan.status = RunStatus.FAILED
                plan.error_message = str(exc)[:1000]
                plan.completed_at = datetime.now(UTC)
                await session.commit()
                return
            await self._finalize_status(session, plan, critical_failed)

    async def _finalize_status(
        self, session: AsyncSession, plan: ResearchPlan, critical_failed: list[str]
    ) -> None:
        """Explicit run status: FAILED > PARTIAL > SUCCEEDED (BACKEND_SPEC)."""
        steps = (
            (await session.execute(
                select(ResearchPlanStep).where(ResearchPlanStep.research_plan_id == plan.id)
            )).scalars().all()
        )
        if critical_failed:
            plan.status = RunStatus.FAILED
            if not plan.error_message:
                plan.error_message = f"critical step failed: {', '.join(critical_failed)}"
            for step in steps:
                if step.status in (RunStatus.QUEUED, RunStatus.RUNNING):
                    step.status = RunStatus.CANCELLED
                    step.error_message = "skipped: a critical step failed"
                    step.completed_at = datetime.now(UTC)
        elif any(s.status == RunStatus.PARTIAL for s in steps):
            plan.status = RunStatus.PARTIAL
            plan.error_message = plan.error_message or None
        else:
            plan.status = RunStatus.SUCCEEDED
        plan.completed_at = datetime.now(UTC)
        await session.commit()

    async def _run_steps(
        self, session: AsyncSession, plan: ResearchPlan, critical_failed: list[str]
    ) -> None:
        result = await session.execute(
            select(ResearchPlanStep).where(ResearchPlanStep.research_plan_id == plan.id)
        )
        by_key = {s.step_key: s for s in result.scalars().all()}
        steps = [by_key[k] for k in STEP_ORDER if k in by_key]
        for step in steps:
            step.status = RunStatus.RUNNING
            step.started_at = datetime.now(UTC)
            await session.commit()
            is_critical = step.step_key in CRITICAL_STEPS
            try:
                output = await self._execute_step(session, plan, step.step_key)
                # Discovery/program waves record their provider calls in
                # search_runs; surface the ids on the step for live activity.
                run_ids = output.pop("search_run_ids", None)
                if isinstance(run_ids, list):
                    step.search_run_ids = [str(r) for r in run_ids]
                step.output = output
                step.status = RunStatus.SUCCEEDED
            except SerpApiError as exc:
                step.error_message = f"{exc.code}: {exc}"
                if exc.code == "PROVIDER_AUTH_ERROR":
                    # Provider auth is global: nothing downstream can search.
                    step.status = RunStatus.FAILED
                    step.completed_at = datetime.now(UTC)
                    await session.commit()
                    raise
                if is_critical:
                    step.status = RunStatus.FAILED
                    critical_failed.append(step.step_key)
                else:
                    step.status = RunStatus.PARTIAL
            except Exception as exc:  # noqa: BLE001
                step.error_message = (str(exc) or repr(exc))[:1000]
                if is_critical:
                    step.status = RunStatus.FAILED
                    critical_failed.append(step.step_key)
                else:
                    # Graceful partial: a non-critical failure must not sink
                    # the run; downstream steps tolerate missing inputs.
                    step.status = RunStatus.PARTIAL
            step.completed_at = datetime.now(UTC)
            await session.commit()
            if is_critical and step.status == RunStatus.FAILED:
                # Do not keep burning provider budget after a critical failure.
                return

    # --------------------------------------------------------------- steps
    async def _execute_step(self, session: AsyncSession, plan: ResearchPlan, step_key: str) -> dict[str, Any]:
        from app.services.profile import get_or_create_profile  # avoid circular import

        profile = await get_or_create_profile(session)
        self._search_locale = await self._search_locale_for(session, profile)
        if step_key == "validate_profile":
            from app.services.profile import validate_profile

            result = validate_profile(profile)
            if not result.valid:
                raise ValueError("; ".join(i.message for i in result.issues))
            return {"valid": True}
        if step_key == "plan_queries":
            from app.db.models import ProfilePreference

            prefs_result = await session.execute(
                select(ProfilePreference).where(ProfilePreference.profile_id == profile.id)
            )
            prefs = prefs_result.scalar_one_or_none()
            prefs_dict = {
                "preferred_countries": prefs.preferred_countries if prefs else [],
                "preferred_cities": prefs.preferred_cities if prefs else [],
            }
            year = int(plan.requested_goal.get("intake_year", datetime.now(UTC).year + 1))
            queries = plan_queries(profile, prefs_dict, year)
            planned = [q.as_dict() for q in queries]
            plan.planned_queries = planned
            await session.commit()
            budgets: dict[str, int] = defaultdict(int)
            engines: dict[str, int] = defaultdict(int)
            for q in queries:
                budgets[q.budget] += 1
                engines[q.engine] += 1
            return {
                "queries": [q.q for q in queries],
                "query_count": len(queries),
                "budgets": dict(budgets),
                "engines": dict(engines),
                "budget_limits": dict(RUN_BUDGETS),
            }
        if step_key == "discovery_search":
            return await self._run_discovery(session, plan, profile)
        if step_key == "normalize_programs":
            return await self._normalize_programs(session, plan)
        if step_key == "extract_evidence":
            return await self._extract_evidence(session, plan)
        if step_key == "evaluate_requirements":
            from app.services.strategy.persist import step_evaluate_requirements

            return await step_evaluate_requirements(session, profile.id)
        if step_key == "score_fit":
            from app.services.strategy.persist import step_score_fit

            return await step_score_fit(session, profile.id, plan.id)
        if step_key == "assess_risks":
            from app.services.strategy.persist import step_assess_risks

            return await step_assess_risks(session, profile.id)
        if step_key == "build_strategy":
            from app.services.strategy.persist import step_build_strategy

            return await step_build_strategy(session, profile.id, plan.id)
        raise ValueError(f"Unknown step {step_key}")

    # ------------------------------------------------- delegating methods
    # Implementation lives in research.search / research.normalize /
    # research.extract; these keep the historical method surface.
    def _planned_waves(self, entries: list[Any]) -> tuple[list[SearchWave], dict[str, int]]:
        return planned_waves(entries)

    async def _fetch_waves(self, waves: list[SearchWave]) -> list[WaveOutcome]:
        return await fetch_waves(self, waves)

    async def _record_outcomes(
        self, session: AsyncSession, outcomes: list[WaveOutcome]
    ) -> tuple[list[dict[str, Any]], list[str], list[uuid.UUID]]:
        return await record_outcomes(self, session, outcomes)

    async def _run_discovery(
        self, session: AsyncSession, plan: ResearchPlan, profile: Any
    ) -> dict[str, Any]:
        return await run_discovery(self, session, plan, profile)

    async def _recent_evidence_fallback(self, session: AsyncSession) -> dict[str, Any]:
        return await recent_evidence_fallback(session)

    async def _upsert_source(
        self, session: AsyncSession, url: str | None, title: str | None
    ) -> Source | None:
        return await upsert_source(session, url, title)

    async def _result_stats(
        self, session: AsyncSession, run_ids: list[uuid.UUID]
    ) -> tuple[int, int]:
        return await result_stats(session, run_ids)

    async def _run_program_queries(
        self,
        session: AsyncSession,
        plan: ResearchPlan,
        shortlist: list[tuple[Program, str | None]],
    ) -> dict[str, Any]:
        return await run_program_queries(self, session, plan, shortlist)

    async def _run_funding_queries(
        self,
        session: AsyncSession,
        plan: ResearchPlan,
        shortlist: list[tuple[Program, str | None]],
    ) -> dict[str, Any]:
        return await run_funding_queries(self, session, plan, shortlist)

    async def _search_locale_for(self, session: AsyncSession, profile: Any) -> dict[str, Any]:
        return await search_locale_for(self, session, profile)

    async def _profile_countries(self, session: AsyncSession, profile: Any) -> list[str]:
        return await profile_countries(session, profile)

    async def _normalize_programs(self, session: AsyncSession, plan: ResearchPlan) -> dict[str, Any]:
        # Deterministic normalization of search results into candidate programs.
        # Only program-intent ("discovery") queries feed programs; policy/news/career
        # results are kept as evidence sources but never become programs.
        return await normalize_programs(self, session, plan)

    async def _shortlist_programs(
        self, session: AsyncSession, limit: int
    ) -> list[tuple[Program, str | None]]:
        return await shortlist_programs(session, limit)

    async def _select_extraction_results(
        self, session: AsyncSession, budget: int
    ) -> list[SearchResult]:
        return await select_extraction_results(session, budget)

    async def _upsert_intakes_from_evidence(self, session: AsyncSession, intake_year: int) -> int:
        return await upsert_intakes_from_evidence(session, intake_year)

    async def _extract_evidence(self, session: AsyncSession, plan: ResearchPlan) -> dict[str, Any]:
        return await extract_evidence(self, session, plan)

    async def _match_program(self, session: AsyncSession, item: SearchResult) -> Program | None:
        return await match_program(session, item)


def _STEP_SERVICES() -> list[tuple[str, str]]:
    return [
        ("validate_profile", "profile"),
        ("plan_queries", "planner"),
        ("discovery_search", "serpapi"),
        ("normalize_programs", "discovery"),
        ("extract_evidence", "evidence"),
        ("evaluate_requirements", "matching"),
        ("score_fit", "scoring"),
        ("assess_risks", "risk"),
        ("build_strategy", "strategy"),
    ]
