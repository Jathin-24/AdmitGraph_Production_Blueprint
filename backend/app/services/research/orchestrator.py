"""Research orchestration: validate, plan, execute bounded searches, persist, advance strategy.

Reliability contract (backend/BACKEND_SPEC.md "Reliability" + serpapi_docs):
- every provider search is bounded by per-purpose budgets (planner.RUN_BUDGETS)
- independent searches run concurrently under asyncio.Semaphore(max_concurrency)
- a Redis-backed cache with in-process fallback caches identical searches
- a run-scoped circuit breaker skips remaining live searches after repeated
  consecutive failures and completes discovery from recent evidence (rule 15)
- step failures classify: critical -> plan FAILED, non-critical -> step PARTIAL
  and the run continues; any PARTIAL step => plan status PARTIAL
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings, get_settings
from app.db.models import (
    ConfidenceLevel,
    Evidence,
    Program,
    ResearchPlan,
    ResearchPlanStep,
    RunStatus,
    SearchResult,
    SearchRun,
    Source,
    SourceAuthority,
)
from app.db.session import get_engine
from app.services.research.planner import (
    MAX_DISCOVERY_QUERIES,
    OFFICIAL_SITE_DISCOVERY_TEMPLATE,
    PROGRAM_BUDGET,
    RUN_BUDGETS,
    PlannedQuery,
    country_display,
    field_display,
    plan_program_queries,
    plan_queries,
)
from app.services.serpapi.authority import classify_domain, is_non_program_domain, looks_like_program_title
from app.services.serpapi.cache import SearchCache
from app.services.serpapi.circuit import CircuitBreaker
from app.services.serpapi.client import SerpApiClient, SerpApiError, SerpApiResult

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

PROGRAM_PURPOSES = ("requirements", "language", "prerequisites", "deadline_tuition")
PAGE2_START = 10  # google pagination: start=10 -> results 11-20
MAX_PAGE2_QUERIES = 2
# Extraction quotas count *bindable* rows only (see
# _select_extraction_results): buckets over-fetch by this factor and trim in
# Python — institution domains and program titles are tiny in-memory sets.
_BINDABLE_OVERFETCH = 4

# LLM claim extraction is bounded to MAX_RESULTS_PER_RUN results per run
# (llm_extract). That budget is split across purposes so a step whose results
# arrive late is not starved: funding/scholarship queries only run once the
# programs are shortlisted (normalize step), and behind hundreds of program
# pages they would otherwise never reach extraction. Shares sum to 10 and are
# relative to the total, so raising MAX_RESULTS_PER_RUN keeps the proportions.
# `None` = every purpose not named above (policy/news/career).
_EXTRACTION_SHARES: tuple[tuple[tuple[str, ...] | None, int], ...] = (
    (PROGRAM_PURPOSES, 6),
    (("discovery",), 2),
    (("funding",), 1),
    (None, 1),
)


def _extraction_quotas(budget: int) -> list[tuple[tuple[str, ...] | None, int]]:
    """Split `budget` results across the purpose buckets (sums to <= budget)."""
    total_shares = sum(share for _purposes, share in _EXTRACTION_SHARES)
    buckets = len(_EXTRACTION_SHARES)
    quotas: list[tuple[tuple[str, ...] | None, int]] = []
    used = 0
    for index, (purposes, share) in enumerate(_EXTRACTION_SHARES):
        want = (budget * share) // total_shares
        if budget >= buckets:
            want = max(1, want)  # every purpose keeps at least one slot
        if index == buckets - 1:
            want = budget - used  # last bucket absorbs rounding
        quota = max(0, min(want, budget - used))
        quotas.append((purposes, quota))
        used += quota
    return quotas


def _to_confidence(raw: str) -> ConfidenceLevel:
    try:
        return ConfidenceLevel(raw.upper())
    except ValueError:
        return ConfidenceLevel.LOW


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


@dataclass
class SearchWave:
    """One planned search to execute (engine + query + bounded parameters)."""

    engine: str
    q: str
    purpose: str
    parameters: dict[str, Any] = field(default_factory=dict)
    template: str | None = None
    budget: str = "discovery"


@dataclass
class WaveOutcome:
    wave: SearchWave
    result: SerpApiResult | None = None
    error: SerpApiError | None = None
    skipped: bool = False  # circuit open: no live request attempted


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

    # ------------------------------------------------------------- searching
    def _planned_waves(self, entries: list[Any]) -> tuple[list[SearchWave], dict[str, int]]:
        """Deserialize plan.planned_queries into waves, enforcing budgets."""
        waves: list[SearchWave] = []
        used: dict[str, int] = defaultdict(int)
        for entry in entries:
            if isinstance(entry, dict):
                q = str(entry.get("q", ""))
                engine = str(entry.get("engine") or "google")
                purpose = str(entry.get("purpose") or "discovery")
                params = entry.get("parameters")
                parameters = dict(params) if isinstance(params, dict) else {}
                template = entry.get("template")
                budget = str(entry.get("budget") or purpose)
            else:  # legacy shape: plain string
                q = str(entry)
                engine = "google_jobs" if "jobs" in q else ("google_news" if "visa policy" in q else "google")
                purpose = "discovery"
                parameters = {}
                template = None
                budget = "discovery"
            if not q:
                continue
            cap = RUN_BUDGETS.get(budget)
            if cap is None:
                cap = PROGRAM_BUDGET if budget.startswith("program:") else None
            if cap is not None and used[budget] >= cap:
                continue  # budget exhausted: bounded per serpapi_docs rule 11
            used[budget] += 1
            waves.append(
                SearchWave(
                    engine=engine,
                    q=q,
                    purpose=purpose,
                    parameters=parameters,
                    template=str(template) if template else None,
                    budget=budget,
                )
            )
        return waves, used

    async def _fetch_waves(self, waves: list[SearchWave]) -> list[WaveOutcome]:
        """Run independent searches concurrently under a bounded semaphore.

        HTTP happens concurrently; DB recording stays sequential (an AsyncSession
        is not concurrency-safe). Circuit-open waves are skipped without a
        provider request.
        """
        semaphore = asyncio.Semaphore(max(1, int(self._settings.serpapi_max_concurrency)))

        async def one(wave: SearchWave) -> WaveOutcome:
            if not self._circuit.allow():
                return WaveOutcome(wave=wave, skipped=True)
            async with semaphore:
                try:
                    result = await self._serpapi.search(
                        wave.engine, wave.q, parameters=wave.parameters, locale=self._search_locale
                    )
                except SerpApiError as exc:
                    self._circuit.record_failure(exc.code)
                    return WaveOutcome(wave=wave, error=exc)
                self._circuit.record_success()
                return WaveOutcome(wave=wave, result=result)

        return list(await asyncio.gather(*(one(w) for w in waves)))

    async def _record_outcomes(
        self, session: AsyncSession, outcomes: list[WaveOutcome]
    ) -> tuple[list[dict[str, Any]], list[str], list[uuid.UUID]]:
        """Persist SearchRun/SearchResult rows (every request is recorded)."""
        results: list[dict[str, Any]] = []
        official_domains: list[str] = []
        run_ids: list[uuid.UUID] = []
        for outcome in outcomes:
            wave = outcome.wave
            if outcome.skipped:
                results.append(
                    {
                        "query": wave.q,
                        "engine": wave.engine,
                        "purpose": wave.purpose,
                        "skipped": "circuit_open",
                    }
                )
                continue
            if outcome.error is not None:
                exc = outcome.error
                run = SearchRun(
                    engine=wave.engine,
                    query=wave.q,
                    parameters={
                        "purpose": wave.purpose,
                        "budget": wave.budget,
                        "template": wave.template,
                        **wave.parameters,
                    },
                    status=RunStatus.FAILED,
                    error_code=exc.code,
                    error_message=str(exc)[:500],
                )
                session.add(run)
                await session.commit()
                results.append({"query": wave.q, "engine": wave.engine, "error": exc.code})
                continue
            result = outcome.result
            assert result is not None
            run = SearchRun(
                engine=wave.engine,
                query=wave.q,
                parameters={
                    "purpose": wave.purpose,
                    "budget": wave.budget,
                    "template": wave.template,
                    **result.parameters,
                },
                serpapi_search_id=result.search_id,
                status=RunStatus.SUCCEEDED,
                duration_ms=result.duration_ms,
                result_count=result.result_count,
                cache_hit=result.cache_hit,
            )
            session.add(run)
            await session.flush()
            run_ids.append(run.id)
            items = result.organic_results + result.news_results + result.jobs_results
            for position, item in enumerate(items[:10], start=1):
                url = item.get("link") or item.get("url")
                source = await self._upsert_source(session, url, item.get("title"))
                if (
                    source is not None
                    and wave.budget == "discovery"
                    and source.source_authority == SourceAuthority.OFFICIAL_UNIVERSITY
                    and source.domain
                    and source.domain not in official_domains
                ):
                    official_domains.append(source.domain)
                session.add(
                    SearchResult(
                        search_run_id=run.id,
                        source_id=source.id if source else None,
                        position=position,
                        result_type=wave.engine,
                        title=item.get("title"),
                        snippet=item.get("snippet"),
                        displayed_url=item.get("displayed_link"),
                        result_url=url,
                        raw_payload=item,
                    )
                )
            await session.commit()
            results.append(
                {
                    "query": wave.q,
                    "engine": wave.engine,
                    "purpose": wave.purpose,
                    "count": result.result_count,
                    "cache_hit": result.cache_hit,
                }
            )
        return results, official_domains, run_ids

    async def _run_discovery(
        self, session: AsyncSession, plan: ResearchPlan, profile: Any
    ) -> dict[str, Any]:
        entries = plan.planned_queries or []
        waves, used = self._planned_waves(entries)

        results: list[dict[str, Any]] = []
        outcomes = await self._fetch_waves(waves)
        recorded, official_domains, run_ids = await self._record_outcomes(session, outcomes)
        results.extend(recorded)

        page2_runs = 0
        official_site_query: str | None = None
        # Wave 2 (inside the discovery budget): official-site query for a domain
        # known from wave-1 discovery results + page-2 for top queries.
        if self._circuit.allow() and waves:
            extra: list[SearchWave] = []
            remaining = MAX_DISCOVERY_QUERIES - used.get("discovery", 0)
            field_name = field_display(getattr(profile, "field_of_study", None) or "graduate")
            if official_domains and remaining > 0:
                official_site_query = OFFICIAL_SITE_DISCOVERY_TEMPLATE.format(
                    field=field_name, official_domain=official_domains[0]
                )
                extra.append(
                    SearchWave(
                        engine="google",
                        q=official_site_query,
                        purpose="discovery",
                        template=OFFICIAL_SITE_DISCOVERY_TEMPLATE,
                        budget="discovery",
                    )
                )
                remaining -= 1
            # Page-2 (start=10) only for the top 1-2 discovery queries, counted
            # inside the discovery budget (serpapi_docs: discovery <= 6/run).
            page2_candidates = [
                w for w in waves if w.budget == "discovery" and w.engine == "google"
            ]
            for wave in page2_candidates[:MAX_PAGE2_QUERIES]:
                if remaining <= 0:
                    break
                extra.append(
                    SearchWave(
                        engine="google",
                        q=wave.q,
                        purpose=wave.purpose,
                        parameters={**wave.parameters, "start": PAGE2_START},
                        template=wave.template,
                        budget="discovery",
                    )
                )
                page2_runs += 1
                remaining -= 1
            if extra:
                extra_outcomes = await self._fetch_waves(extra)
                extra_recorded, _, extra_ids = await self._record_outcomes(session, extra_outcomes)
                results.extend(extra_recorded)
                run_ids.extend(extra_ids)

        searches_run = len(run_ids)
        results_stored = (
            await session.execute(
                select(SearchResult.id).where(SearchResult.search_run_id.in_(run_ids)).limit(1000)
            )
        ).all() if run_ids else []
        domains: set[str] = set()
        if run_ids:
            domain_rows = await session.execute(
                select(Source.domain)
                .join(SearchResult, SearchResult.source_id == Source.id)
                .where(SearchResult.search_run_id.in_(run_ids))
            )
            domains = {str(d) for d in domain_rows.all() if d}

        from app.services.research.career import career_summary, record_career_evidence
        from app.services.research.policy import policy_summary, record_policy_evidence

        # Career/policy signals become first-class evidence rows (claim types
        # "career"/"policy", evidence-only keys) right here in the discovery
        # step, so the scoring dimensions can consult them instead of always
        # answering UNKNOWN. Both recorders read STORED rows only — provider
        # errors and zero results simply yield honest zeros.
        output: dict[str, Any] = {
            "searches": results,
            "searches_run": searches_run,
            "results_stored": len(results_stored),
            "sources": len(domains),
            "search_run_ids": [str(r) for r in run_ids],
            "page2_queries": page2_runs,
            "official_site_query": official_site_query,
            "discovery_budget": MAX_DISCOVERY_QUERIES,
            "circuit_state": self._circuit.state,
            "career": {
                **career_summary(results),
                **await record_career_evidence(session, search_run_ids=run_ids),
            },
            "policy": {
                **policy_summary(results),
                **await record_policy_evidence(session, search_run_ids=run_ids),
            },
        }
        if not self._circuit.allow():
            # serpapi_docs rule 15: provider failing -> complete discovery from
            # recent evidence inside its freshness window, labeled as cached.
            fallback = await self._recent_evidence_fallback(session)
            output.update({"circuit_open": True, **fallback})
        return output

    async def _recent_evidence_fallback(self, session: AsyncSession) -> dict[str, Any]:
        now = datetime.now(UTC)
        rows = (
            await session.execute(
                select(Evidence)
                .where(Evidence.freshness_deadline.is_not(None), Evidence.freshness_deadline > now)
                .order_by(Evidence.retrieved_at.desc())
                .limit(25)
            )
        ).scalars().all()
        return {
            "source": "recent_evidence",
            "cached": True,
            "recent_evidence_claims": len(rows),
            "recent_evidence_ids": [str(r.id) for r in rows],
        }

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

    # ------------------------------------------------------------- programs
    async def _normalize_programs(self, session: AsyncSession, plan: ResearchPlan) -> dict[str, Any]:
        # Deterministic normalization of search results into candidate programs.
        # Only program-intent ("discovery") queries feed programs; policy/news/career
        # results are kept as evidence sources but never become programs.
        rows = await session.execute(
            select(SearchResult)
            .join(SearchRun, SearchResult.search_run_id == SearchRun.id)
            .where(SearchRun.engine == "google")
            .where(SearchRun.parameters["purpose"].astext == "discovery")
            .order_by(SearchResult.position.asc().nulls_last(), SearchResult.id.asc())
            .limit(30)
        )
        created = 0
        for item in rows.scalars():
            if not item.title:
                continue
            name = item.title.split(" - ")[0][:200]
            normalized = name.strip().lower()
            if not normalized:
                continue
            # Title intent gate: only results whose title names a degree
            # program (M.Sc., Master, ...) become programs — guides,
            # rankings and requirement roundups stay evidence only.
            if not looks_like_program_title(name):
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
            # Never normalize news/social/unknown domains into programs; they may
            # still contribute evidence, but they are not study programs.
            if source.source_authority in (
                SourceAuthority.FORUM_SOCIAL,
                SourceAuthority.NEWS,
                SourceAuthority.UNKNOWN,
            ):
                continue
            # Curated non-program platforms (listicles, document hosts, ranking
            # sites) are evidence-worthy at most — never degree programs.
            if source.domain and is_non_program_domain(source.domain):
                continue
            inst_name = (source.domain or name).lower()
            inst = (
                (
                    await session.execute(
                        select(Institution)
                        .where(Institution.normalized_name == inst_name)
                        .where(Institution.country_code.is_(None))
                        .limit(1)
                    )
                )
                .scalars()
                .first()
            )
            if inst is None:
                inst = Institution(
                    canonical_name=source.domain or name,
                    normalized_name=inst_name,
                    domain=source.domain,
                )
                session.add(inst)
                await session.flush()
            session.add(Program(institution_id=inst.id, canonical_name=name, normalized_name=normalized))
            created += 1
            if created >= 10:
                break
        await session.commit()

        # Shortlist top programs and run their bounded official-site queries
        # (plan_program_queries is wired into real runs), then spend the
        # remaining funding budget on the reserved site: scholarship query.
        shortlist = await self._shortlist_programs(session, limit=4)
        program_output = await self._run_program_queries(session, plan, shortlist)
        funding_output = await self._run_funding_queries(session, plan, shortlist)
        program_run_ids = list(program_output.pop("search_run_ids", None) or [])
        funding_run_ids = list(funding_output.pop("search_run_ids", None) or [])
        output: dict[str, Any] = {"programs_created": created, **program_output, **funding_output}
        search_run_ids = [*program_run_ids, *funding_run_ids]
        if search_run_ids:
            output["search_run_ids"] = search_run_ids
        return output

    async def _result_stats(
        self, session: AsyncSession, run_ids: list[uuid.UUID]
    ) -> tuple[int, int]:
        """(stored results, distinct source domains) for the given search runs."""
        if not run_ids:
            return 0, 0
        stored_rows = await session.execute(
            select(SearchResult.id).where(SearchResult.search_run_id.in_(run_ids))
        )
        domain_rows = await session.execute(
            select(Source.domain)
            .join(SearchResult, SearchResult.source_id == Source.id)
            .where(SearchResult.search_run_id.in_(run_ids))
        )
        domains = {str(d) for d in domain_rows.all() if d}
        return len(stored_rows.all()), len(domains)

    async def _run_program_queries(
        self,
        session: AsyncSession,
        plan: ResearchPlan,
        shortlist: list[tuple[Program, str | None]],
    ) -> dict[str, Any]:
        """Run <= 4 official-site queries per shortlisted program (<= 4 programs),
        within the per-program budget."""
        intake_year = int(plan.requested_goal.get("intake_year", datetime.now(UTC).year + 1))
        waves: list[SearchWave] = []
        for program, domain in shortlist:
            for pq in plan_program_queries(program.canonical_name, domain, intake_year):
                waves.append(
                    SearchWave(
                        engine=pq.engine,
                        q=pq.q,
                        purpose=pq.purpose,
                        parameters=dict(pq.parameters),
                        template=pq.template,
                        budget=pq.budget,
                    )
                )
        if not waves:
            return {"shortlisted_programs": [], "program_queries_run": 0}
        outcomes = await self._fetch_waves(waves)
        _recorded, _, run_ids = await self._record_outcomes(session, outcomes)
        results_stored, sources = await self._result_stats(session, run_ids)
        return {
            "shortlisted_programs": [
                {"program_id": str(p.id), "name": p.canonical_name, "official_domain": d}
                for p, d in shortlist
            ],
            "program_queries_run": len([o for o in outcomes if not o.skipped]),
            "program_queries_budget_per_program": PROGRAM_BUDGET,
            "results_stored": results_stored,
            "sources": sources,
            "search_run_ids": [str(r) for r in run_ids],
        }

    async def _run_funding_queries(
        self,
        session: AsyncSession,
        plan: ResearchPlan,
        shortlist: list[tuple[Program, str | None]],
    ) -> dict[str, Any]:
        """Issue the reserved site-targeted scholarship query (funding budget).

        The planner already spent one funding slot on the non-site country
        fallback, which runs with the discovery wave. The remaining slot goes to
        `site:{official_domain} international students scholarship {program}`
        for a shortlisted program; when no official domain is known, to a
        profile country target the planned fallback did not already cover.
        Bounded by MAX_FUNDING_QUERIES per run and deduplicated against the
        queries already planned for this run (serpapi_docs "API usage budget").
        Funding stays non-critical: a failed query is recorded as a FAILED
        search run and the step continues (PARTIAL classification unchanged).
        """
        from app.services.profile import get_or_create_profile  # avoid circular import
        from app.services.research.funding import scholarship_query_for
        from app.services.research.planner import MAX_FUNDING_QUERIES, country_display

        entries = [e for e in (plan.planned_queries or []) if isinstance(e, dict)]
        issued = [
            str(e.get("q", "")).strip().lower()
            for e in entries
            if str(e.get("budget") or e.get("purpose") or "") == "funding"
        ]
        remaining = MAX_FUNDING_QUERIES - len(issued)
        if remaining <= 0:
            return {
                "funding_queries_run": 0,
                "funding_queries": [],
                "funding_budget": MAX_FUNDING_QUERIES,
                "funding_budget_remaining": 0,
            }

        profile = await get_or_create_profile(session)
        countries = await self._profile_countries(session, profile)
        field_name = field_display(getattr(profile, "field_of_study", None) or "graduate")
        country_name = country_display(countries[0]) if countries else ""
        seen: set[str] = set(issued)
        waves: list[SearchWave] = []

        def add(query: PlannedQuery | None) -> None:
            """Budget + dedup gate: one wave per distinct, unbudgeted query."""
            if query is None or len(waves) >= remaining:
                return
            key = query.q.strip().lower()
            if key in seen:
                return
            seen.add(key)
            waves.append(
                SearchWave(
                    engine=query.engine,
                    q=query.q,
                    purpose=query.purpose,
                    parameters=dict(query.parameters),
                    template=query.template,
                    budget=query.budget,
                )
            )

        # 1) shortlisted programs: site-restricted to their official domain.
        for program, domain in shortlist:
            add(
                scholarship_query_for(
                    program.canonical_name,
                    domain,
                    country_name,
                    field_name,
                    budget_remaining=remaining - len(waves),
                )
            )
            if len(waves) >= remaining:
                break
        # 2) no domain known: another profile country target (deduplicated
        #    against the fallback query the planner already issued).
        if not waves:
            for code in countries:
                add(
                    scholarship_query_for(
                        "",
                        None,
                        country_display(code),
                        field_name,
                        budget_remaining=remaining - len(waves),
                    )
                )
                if len(waves) >= remaining:
                    break

        if not waves:
            return {
                "funding_queries_run": 0,
                "funding_queries": [],
                "funding_budget": MAX_FUNDING_QUERIES,
                "funding_budget_remaining": remaining,
            }

        outcomes = await self._fetch_waves(waves)
        _recorded, _, run_ids = await self._record_outcomes(session, outcomes)
        results_stored, sources = await self._result_stats(session, run_ids)
        return {
            "funding_queries_run": len([o for o in outcomes if not o.skipped]),
            "funding_queries": [w.q for w in waves],
            "funding_budget": MAX_FUNDING_QUERIES,
            "funding_budget_remaining": remaining - len(waves),
            "funding_results_stored": results_stored,
            "funding_sources": sources,
            "search_run_ids": [str(r) for r in run_ids],
        }

    async def _search_locale_for(self, session: AsyncSession, profile: Any) -> dict[str, Any]:
        """SerpApi localization params (SerpApi "Easy Integration" docs):
        hl/gl/location/google_domain for the profile's primary target country.

        Sent on the wire only — deliberately excluded from the client's cache
        key so the warm 6-hour cache stays valid across locales (the search
        budget is the scarce resource). English UI, US fallback.
        """
        countries = await self._profile_countries(session, profile)
        code = countries[0].strip().lower() if countries else "us"
        locale: dict[str, Any] = {"hl": "en", "gl": code, "google_domain": "google.com"}
        name = country_display(countries[0].strip()) if countries else ""
        if name and name != countries[0].strip():
            # `location` only for known country names: SerpApi rejects unknown
            # location strings, which must never fail a run.
            locale["location"] = name
        return locale

    async def _profile_countries(self, session: AsyncSession, profile: Any) -> list[str]:
        """Profile country targets, resolved the same way the planner resolves them."""
        from app.db.models import ProfilePreference

        prefs = (
            await session.execute(
                select(ProfilePreference).where(ProfilePreference.profile_id == profile.id)
            )
        ).scalar_one_or_none()
        countries = [str(c) for c in ((prefs.preferred_countries if prefs else []) or []) if str(c).strip()]
        if not countries:
            code = (getattr(profile, "institution_country_code", None) or "").strip()
            countries = [code] if code else []
        return countries

    async def _shortlist_programs(
        self, session: AsyncSession, limit: int
    ) -> list[tuple[Program, str | None]]:
        """Rank programs by discovery order weighted by source authority."""
        authority_rank = {
            SourceAuthority.OFFICIAL_UNIVERSITY: 0,
            SourceAuthority.OFFICIAL_GOVERNMENT: 1,
            SourceAuthority.ACCREDITED_BODY: 2,
            SourceAuthority.OFFICIAL_ORGANIZATION: 3,
            SourceAuthority.CREDIBLE_SECONDARY: 4,
        }
        rows = (
            await session.execute(
                select(SearchResult, Source)
                .join(SearchRun, SearchResult.search_run_id == SearchRun.id)
                .join(Source, SearchResult.source_id == Source.id, isouter=True)
                .where(SearchRun.parameters["purpose"].astext == "discovery")
                .order_by(SearchResult.position.asc().nulls_last(), SearchResult.id.asc())
                .limit(60)
            )
        ).all()
        seen: dict[uuid.UUID, tuple[int, Program, str | None]] = {}
        for result, source in rows:
            if not result.title:
                continue
            normalized = result.title.split(" - ")[0].strip().lower()
            if not normalized:
                continue
            program = (
                await session.execute(select(Program).where(Program.normalized_name == normalized))
            ).scalar_one_or_none()
            if program is None:
                continue
            rank = authority_rank.get(source.source_authority if source else SourceAuthority.UNKNOWN, 5)
            if program.id not in seen or rank < seen[program.id][0]:
                domain = source.domain if source else None
                if domain and domain.startswith("www."):
                    domain = domain[4:]
                seen[program.id] = (rank, program, domain)
        ranked = sorted(seen.values(), key=lambda t: t[0])[:limit]
        shortlist = [(program, domain) for _rank, program, domain in ranked]
        if not shortlist:
            fallback = (
                await session.execute(
                    select(Program).order_by(Program.first_seen_at).limit(limit)
                )
            ).scalars().all()
            from app.db.models import Institution

            for program in fallback:
                institution = await session.get(Institution, program.institution_id)
                shortlist.append((program, institution.domain if institution else None))
        return shortlist

    # ------------------------------------------------------------- evidence
    async def _select_extraction_results(
        self, session: AsyncSession, budget: int
    ) -> list[SearchResult]:
        """Bounded, purpose-aware, bindable-only window (<= `budget` rows).

        Program pages (deadline/tuition/language) keep the largest share,
        discovery the next; funding — the reserved scholarship slot — and
        everything else share the tail. Within a bucket the newest rows win.

        A row enters the window only when a claim from it could carry a
        subject: its source domain belongs to a catalog institution, or its
        title names an existing program. Subject-less claims from unbindable
        hosts (social/listicle/aggregator pages, `site:` results Google
        dropped off-site) would be stored forever without ever feeding
        requirements or scoring, so they never spend LLM budget.
        """
        from app.db.models import Institution

        inst_domains = [
            (d or "").strip().lower().removeprefix("www.")
            for (d,) in (await session.execute(select(Institution.domain))).all()
            if (d or "").strip()
        ]
        program_names = set(
            (await session.execute(select(Program.normalized_name))).scalars().all()
        )

        def bindable(row: SearchResult, domain: str | None) -> bool:
            d = (domain or "").strip().lower().removeprefix("www.")
            if d and any(d == b or d.endswith("." + b) or b.endswith("." + d) for b in inst_domains):
                return True
            if row.title:
                return row.title.split(" - ")[0].strip().lower() in program_names
            return False

        selected: list[SearchResult] = []
        for purposes, quota in _extraction_quotas(budget):
            if quota <= 0:
                continue
            stmt = (
                select(SearchResult)
                .join(SearchRun, SearchResult.search_run_id == SearchRun.id)
                .where(SearchResult.source_id.is_not(None))
            )
            if purposes is None:
                named = [*PROGRAM_PURPOSES, "discovery", "funding"]
                purpose_col = SearchRun.parameters["purpose"].astext
                stmt = stmt.where(
                    or_(purpose_col.is_(None), purpose_col.notin_(named)),
                )
            else:
                stmt = stmt.where(SearchRun.parameters["purpose"].astext.in_(list(purposes)))
            # Most recent queries first: SearchResult.id is a random UUID (not
            # monotonic), so recency comes from the search run's timestamp —
            # this run's official-site program pages must win the window.
            rows = (
                await session.execute(
                    stmt.order_by(
                        SearchRun.requested_at.desc(),
                        SearchResult.position.asc().nulls_last(),
                    ).limit(quota * _BINDABLE_OVERFETCH)
                )
            ).scalars().all()
            source_ids = [r.source_id for r in rows if r.source_id is not None]
            sources: dict[uuid.UUID, Source] = {}
            if source_ids:
                sources = {
                    s.id: s
                    for s in (
                        await session.execute(select(Source).where(Source.id.in_(source_ids)))
                    ).scalars()
                }
            kept = [
                row
                for row in rows
                if bindable(
                    row,
                    sources[row.source_id].domain if row.source_id in sources else None,
                )
            ][:quota]
            selected.extend(kept)
        return selected

    async def _upsert_intakes_from_evidence(self, session: AsyncSession, intake_year: int) -> int:
        """Mint Intake rows from STORED deadline evidence (idempotent).

        Runs on every extract pass: a deadline claimed in an earlier run must
        still produce its intake, instead of depending on the claim happening
        to be re-extracted today. Values without a parseable date (prose,
        term/year only) stay UNKNOWN and mint nothing.
        """
        from app.db.models import Evidence
        from app.services.evidence.extraction import parse_deadline_date, upsert_intake_from_deadline

        rows = (
            await session.execute(
                select(Evidence)
                .where(
                    Evidence.subject_type == "program",
                    Evidence.subject_id.is_not(None),
                    Evidence.normalized_claim == "application_deadline",
                )
                # Oldest first: an upsert overwrites, so the freshest claim
                # is applied last and deterministically wins.
                .order_by(Evidence.retrieved_at.asc(), Evidence.id.asc())
            )
        ).scalars().all()

        # intakes.program_id is a FK: skip evidence whose subject never existed
        # or was deleted (evidence.subject_id is intentionally FK-less) rather
        # than poisoning the session with a violation — same rule as the
        # requirements sync.
        subject_ids = {row.subject_id for row in rows if row.subject_id is not None}
        known_program_ids: set[uuid.UUID] = set()
        if subject_ids:
            known_program_ids = set(
                (await session.execute(select(Program.id).where(Program.id.in_(subject_ids)))).scalars()
            )

        created = 0
        for row in rows:
            if row.subject_id is None or row.subject_id not in known_program_ids:
                continue
            deadline = parse_deadline_date(row.extracted_value or {})
            if deadline is None:
                continue
            _intake, was_created = await upsert_intake_from_deadline(
                session,
                program_id=row.subject_id,
                deadline=deadline,
                intake_year=intake_year,
                evidence_id=row.id,
            )
            created += int(was_created)
        return created

    async def _extract_evidence(self, session: AsyncSession, plan: ResearchPlan) -> dict[str, Any]:
        """LLM claim extraction from recent search results -> Evidence rows -> Requirements."""
        from app.services.evidence.extraction import (
            EvidenceExtractionService,
            ExtractedClaim,
            apply_program_profile,
            parse_deadline_date,
            upsert_intake_from_deadline,
        )
        from app.services.evidence.llm_extract import MAX_RESULTS_PER_RUN, extract_claims
        from app.services.evidence.requirements import sync_requirements_from_evidence
        from app.services.llm import get_llm_provider

        provider = get_llm_provider(self._settings)
        intake_year = int(plan.requested_goal.get("intake_year", datetime.now(UTC).year + 1))
        rows = await self._select_extraction_results(session, MAX_RESULTS_PER_RUN)

        extraction = EvidenceExtractionService()
        claims_created = 0
        intakes_written = 0
        profiles_updated = 0
        profiles_failed = 0
        touched_programs: set[uuid.UUID] = set()
        for item in rows:
            source = await session.get(Source, item.source_id)
            if source is None:
                continue
            llm_claims = await extract_claims(
                provider,
                title=item.title,
                snippet=item.snippet,
                domain=source.domain,
            )
            if llm_claims is None or not llm_claims.claims:
                continue
            program = await self._match_program(session, item)
            # Captured before the optional enrichment below: a rolled-back
            # savepoint expires the instance, so any later attribute access
            # would lazily load outside the greenlet (MissingGreenlet).
            program_id = program.id if program is not None else None
            extracted = [
                ExtractedClaim(
                    claim_type=c.claim_type,
                    normalized_key=c.normalized_key,
                    value=c.value,
                    claim=c.claim,
                    confidence=_to_confidence(c.confidence),
                    subject_type="program",
                    subject_id=program_id,
                )
                for c in llm_claims.claims
            ]
            stored = await extraction.record_claims(
                session, source, item, extracted, extraction_model=None
            )
            claims_created += len(extracted)
            if program is None or program_id is None:
                continue
            touched_programs.add(program_id)

            # Program profile enrichment: only non-null, sufficiently-confident
            # fields are written; UNKNOWN fields stay untouched.
            profile_obj = llm_claims.program_profile
            if profile_obj is not None:
                try:
                    # Savepoint: enrichment is optional, so a rejected write must
                    # not poison the session (claims, intakes and the rest of the
                    # run keep going; the failure is counted, not hidden).
                    async with session.begin_nested():
                        outcome = await apply_program_profile(
                            session,
                            program,
                            profile_obj.model_dump(),
                            confidence=llm_claims.max_confidence,
                        )
                except Exception as exc:  # noqa: BLE001 - optional enrichment
                    profiles_failed += 1
                    logger.warning("program profile enrichment failed for %s: %r", program_id, exc)
                else:
                    profiles_updated += len(outcome.get("updated_fields", []))

            # Deadline claims -> Intake rows (idempotent per program+intake_year).
            for claim, evidence_row in zip(llm_claims.claims, stored, strict=False):
                if claim.normalized_key != "application_deadline":
                    continue
                deadline = parse_deadline_date(claim.value)
                if deadline is None:
                    continue
                _intake, created_intake = await upsert_intake_from_deadline(
                    session,
                    program_id=program_id,
                    deadline=deadline,
                    intake_year=intake_year,
                    evidence_id=evidence_row.id,
                )
                if created_intake:
                    intakes_written += 1

        synced = await sync_requirements_from_evidence(session)

        # Intakes mint from STORED deadline evidence too: an earlier run's
        # deadline claim still produces its intake today (idempotent), so the
        # counter reflects reality instead of today's re-extraction luck.
        intakes_written += await self._upsert_intakes_from_evidence(session, intake_year)

        # Conflict detection is now wired into the pipeline (was never called):
        # differing values for the same normalized key -> CONFLICTING + downgrade.
        from app.services.evidence.conflicts import ConflictDetectionService

        conflicts_detected = 0
        detector = ConflictDetectionService()
        for program_id in touched_programs:
            conflicts = await detector.detect_for_subject(session, "program", program_id)
            conflicts_detected += len(conflicts)

        from app.services.research.funding import scholarship_summary

        funding_out = await scholarship_summary(session, sorted(touched_programs))
        return {
            "claims_created": claims_created,
            "requirements_synced": synced,
            "results_processed": len(rows),
            "program_profiles_updated": profiles_updated,
            "program_profiles_failed": profiles_failed,
            "intakes_written": intakes_written,
            "conflicts_detected": conflicts_detected,
            "scholarship_claims": funding_out["scholarship_claims"],
        }

    async def _match_program(self, session: AsyncSession, item: SearchResult) -> Program | None:
        """Link a search result to the program it was collected for.

        Exact normalized-title match first (the discovery result that created
        the program). Fallback: a result from an institution's own domain
        binds to that institution when it has exactly one program — site:
        queries return page titles that differ from the discovery title.
        """
        from app.db.models import Institution

        if item.title:
            normalized = item.title.split(" - ")[0].strip().lower()
            if normalized:
                result = await session.execute(
                    select(Program).where(Program.normalized_name == normalized).limit(1)
                )
                program = result.scalar_one_or_none()
                if program is not None:
                    return program
        if item.source_id is None:
            return None
        source = await session.get(Source, item.source_id)
        if source is None or not source.domain:
            return None
        # Institutions are stored with heterogeneous domains (program root,
        # subdomain like www.ai.study.fau.eu, sometimes with www.). Match the
        # source domain's registrable suffixes so official-site subpages bind.
        candidates: set[str] = set()
        parts = source.domain.lower().split(".")
        for i in range(len(parts) - 1):
            suffix = ".".join(parts[i:])
            candidates.add(suffix)
            candidates.add("www." + suffix)
        rows = (
            (
                await session.execute(
                    select(Program)
                    .join(Institution, Institution.id == Program.institution_id)
                    .where(Institution.domain.in_(sorted(candidates)))
                    .limit(2)
                )
            )
            .scalars()
            .all()
        )
        # Ambiguous domains (several programs of one institution) stay unbound
        # rather than guessing which page a claim belongs to.
        return rows[0] if len(rows) == 1 else None


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


_background_tasks: set[asyncio.Task[None]] = set()


def dispatch_plan(plan_id: uuid.UUID) -> None:
    from app.core.runcontext import run_id_scope

    # Bind run_id BEFORE create_task: the Task copies the current context, so
    # every orchestrator log line inside the run carries run_id
    # (BACKEND_SPEC §Reliability "structured logs with request_id and run_id").
    with run_id_scope(str(plan_id)):
        service = ResearchService()
        task = asyncio.create_task(service.execute_plan(plan_id))
        _background_tasks.add(task)
        task.add_done_callback(_background_tasks.discard)

        def _finished(t: asyncio.Task[None]) -> None:
            from app.core.events import emit  # local: keeps orchestrator import-light

            emit("research.run_finished", plan_id=plan_id, task=t)

        task.add_done_callback(_finished)
