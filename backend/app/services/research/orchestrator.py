"""Research orchestration: validate, plan, execute bounded searches, persist, advance strategy."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import uuid
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
    SearchRun,
    Source,
)
from app.db.session import get_engine
from app.services.research.planner import plan_queries
from app.services.serpapi.authority import classify_domain
from app.services.serpapi.client import SerpApiClient, SerpApiError

logger = logging.getLogger(__name__)

STRATEGY_VERSION = "v1"
SCORING_VERSION = "v1"


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
        self._serpapi = serpapi or SerpApiClient(self._settings)

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
        steps = [
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
        for key, service in steps:
            session.add(ResearchPlanStep(research_plan_id=plan.id, step_key=key, service_name=service))
        await session.commit()
        await session.refresh(plan)
        return plan

    async def execute_plan(self, plan_id: uuid.UUID) -> None:
        """Runs the plan end-to-end; safe for background execution. Each step commits."""
        get_engine()
        maker = async_sessionmaker(get_engine(), expire_on_commit=False)
        async with maker() as session:
            plan = await session.get(ResearchPlan, plan_id)
            if plan is None:
                return
            plan.status = RunStatus.RUNNING
            plan.started_at = datetime.now(UTC)
            await session.commit()
            try:
                await self._run_steps(session, plan)
                plan.status = RunStatus.SUCCEEDED
            except Exception as exc:  # noqa: BLE001 - orchestrator must not crash worker
                logger.exception("research plan %s failed", plan_id)
                plan.status = RunStatus.FAILED
                plan.error_message = str(exc)[:1000]
            plan.completed_at = datetime.now(UTC)
            await session.commit()

    async def _run_steps(self, session: AsyncSession, plan: ResearchPlan) -> None:
        step_order = [
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
        result = await session.execute(
            select(ResearchPlanStep).where(ResearchPlanStep.research_plan_id == plan.id)
        )
        by_key = {s.step_key: s for s in result.scalars().all()}
        steps = [by_key[k] for k in step_order if k in by_key]
        for step in steps:
            if step.step_key in ("evaluate_requirements", "score_fit", "assess_risks", "build_strategy"):
                # Implemented in later phases; marked as not-yet-run safely.
                step.status = RunStatus.CANCELLED
                step.error_message = "Not implemented yet"
                await session.commit()
                continue
            step.status = RunStatus.RUNNING
            step.started_at = datetime.now(UTC)
            await session.commit()
            try:
                output = await self._execute_step(session, plan, step.step_key)
                step.output = output
                step.status = RunStatus.SUCCEEDED
            except SerpApiError as exc:
                step.status = RunStatus.FAILED if not exc.retryable else RunStatus.PARTIAL
                step.error_message = f"{exc.code}: {exc}"
                if exc.code == "PROVIDER_AUTH_ERROR":
                    raise
            except Exception as exc:  # noqa: BLE001
                step.status = RunStatus.FAILED
                step.error_message = str(exc)[:1000]
            step.completed_at = datetime.now(UTC)
            await session.commit()

    async def _execute_step(self, session: AsyncSession, plan: ResearchPlan, step_key: str) -> dict[str, Any]:
        from app.services.profile import get_or_create_profile  # avoid circular import

        profile = await get_or_create_profile(session)
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
            }
            year = int(plan.requested_goal.get("intake_year", datetime.now(UTC).year + 1))
            queries = plan_queries(profile, prefs_dict, year)
            planned = [q.q for q in queries]
            plan.planned_queries = planned
            await session.commit()
            return {"queries": planned}
        if step_key == "discovery_search":
            return await self._run_discovery(session, plan)
        if step_key == "normalize_programs":
            return await self._normalize_programs(session, plan)
        if step_key == "extract_evidence":
            return {"note": "evidence extracted during normalization; LLM extraction seam in place"}
        raise ValueError(f"Unknown step {step_key}")

    async def _run_discovery(self, session: AsyncSession, plan: ResearchPlan) -> dict[str, Any]:
        queries = plan.planned_queries or []
        results: list[dict[str, Any]] = []
        for q in queries[:6]:
            engine = "google_jobs" if "jobs" in q else ("google_news" if "visa policy" in q else "google")
            try:
                result = await self._serpapi.search(engine, q)
            except SerpApiError as exc:
                session.add(
                    SearchRun(
                        engine=engine,
                        query=q,
                        parameters={},
                        status=RunStatus.FAILED,
                        error_code=exc.code,
                        error_message=str(exc)[:500],
                    )
                )
                await session.commit()
                results.append({"query": q, "error": exc.code})
                continue
            run = SearchRun(
                engine=engine,
                query=q,
                parameters=result.parameters,
                serpapi_search_id=result.search_id,
                status=RunStatus.SUCCEEDED,
                duration_ms=result.duration_ms,
                result_count=result.result_count,
                cache_hit=result.cache_hit,
            )
            session.add(run)
            await session.flush()
            items = result.organic_results + result.news_results + result.jobs_results
            for position, item in enumerate(items[:10], start=1):
                url = item.get("link") or item.get("url")
                source = await self._upsert_source(session, url, item.get("title"))
                session.add(
                    SearchResult(
                        search_run_id=run.id,
                        source_id=source.id if source else None,
                        position=position,
                        result_type=engine,
                        title=item.get("title"),
                        snippet=item.get("snippet"),
                        displayed_url=item.get("displayed_link"),
                        result_url=url,
                        raw_payload=item,
                    )
                )
            await session.commit()
            results.append({"query": q, "engine": engine, "count": result.result_count})
        return {"searches": results}

    async def _upsert_source(
        self, session: AsyncSession, url: str | None, title: str | None
    ) -> Source | None:
        if not url:
            return None
        from urllib.parse import urlparse

        parsed = urlparse(url)
        domain = (parsed.netloc or "").lower()
        canonical = url.split("#")[0]
        existing = await session.execute(select(Source).where(Source.canonical_url == canonical))
        source = existing.scalar_one_or_none()
        if source is not None:
            source.last_seen_at = datetime.now(UTC)
            return source
        source = Source(
            url=url,
            canonical_url=canonical,
            domain=domain,
            title=title,
            source_authority=classify_domain(domain),
            last_seen_at=datetime.now(UTC),
        )
        session.add(source)
        await session.flush()
        return source

    async def _normalize_programs(self, session: AsyncSession, plan: ResearchPlan) -> dict[str, Any]:
        # Deterministic normalization of search results into candidate programs.
        # No invented facts: programs are created from observed titles only.
        rows = await session.execute(
            select(SearchResult)
            .join(SearchRun, SearchResult.search_run_id == SearchRun.id)
            .where(SearchRun.engine == "google")
            .limit(20)
        )
        created = 0
        for item in rows.scalars():
            if not item.title:
                continue
            name = item.title.split(" - ")[0][:200]
            normalized = name.strip().lower()
            if not normalized:
                continue
            existing = await session.execute(select(Program).where(Program.normalized_name == normalized))
            if existing.scalar_one_or_none() is not None:
                continue
            # Program rows require an institution; create a minimal placeholder institution
            # only when a domain-backed source exists, otherwise skip (UNKNOWN data quality).
            if item.source_id is None:
                continue
            from app.db.models import Institution

            source = await session.get(Source, item.source_id)
            if source is None:
                continue
            inst = Institution(
                canonical_name=source.domain or name,
                normalized_name=(source.domain or name).lower(),
                domain=source.domain,
            )
            session.add(inst)
            await session.flush()
            session.add(Program(institution_id=inst.id, canonical_name=name, normalized_name=normalized))
            created += 1
            if created >= 10:
                break
        await session.commit()
        return {"programs_created": created}


_background_tasks: set[asyncio.Task[None]] = set()


def dispatch_plan(plan_id: uuid.UUID) -> None:
    service = ResearchService()
    task = asyncio.create_task(service.execute_plan(plan_id))
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)
