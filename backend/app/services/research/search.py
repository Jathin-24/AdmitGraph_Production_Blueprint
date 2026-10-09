"""
Search execution for research plans (W7 split from research.orchestrator).

Planned waves enforce the per-purpose budgets, independent searches run
concurrently under a semaphore (DB recording stays sequential), and every
attempt -- success, provider error or circuit-open skip -- is recorded.
Functions take the owning ResearchService for provider/circuit access.
"""

from __future__ import annotations

import asyncio
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    Evidence,
    Program,
    ResearchPlan,
    RunStatus,
    SearchResult,
    SearchRun,
    Source,
    SourceAuthority,
)
from app.services.research.planner import (
    MAX_DISCOVERY_QUERIES,
    OFFICIAL_SITE_DISCOVERY_TEMPLATE,
    PROGRAM_BUDGET,
    RUN_BUDGETS,
    PlannedQuery,
    country_display,
    field_display,
    plan_program_queries,
)
from app.services.serpapi.authority import classify_domain
from app.services.serpapi.client import SerpApiError, SerpApiResult

if TYPE_CHECKING:
    from app.services.research.runner import ResearchService


PAGE2_START = 10  # google pagination: start=10 -> results 11-20
MAX_PAGE2_QUERIES = 2


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


# ------------------------------------------------------------- searching
def planned_waves(entries: list[Any]) -> tuple[list[SearchWave], dict[str, int]]:
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


async def fetch_waves(service: ResearchService, waves: list[SearchWave]) -> list[WaveOutcome]:
    """Run independent searches concurrently under a bounded semaphore.

    HTTP happens concurrently; DB recording stays sequential (an AsyncSession
    is not concurrency-safe). Circuit-open waves are skipped without a
    provider request.
    """
    semaphore = asyncio.Semaphore(max(1, int(service._settings.serpapi_max_concurrency)))

    async def one(wave: SearchWave) -> WaveOutcome:
        if not service._circuit.allow():
            return WaveOutcome(wave=wave, skipped=True)
        async with semaphore:
            try:
                result = await service._serpapi.search(
                    wave.engine, wave.q, parameters=wave.parameters, locale=service._search_locale
                )
            except SerpApiError as exc:
                service._circuit.record_failure(exc.code)
                return WaveOutcome(wave=wave, error=exc)
            service._circuit.record_success()
            return WaveOutcome(wave=wave, result=result)

    return list(await asyncio.gather(*(one(w) for w in waves)))


async def record_outcomes(
    service: ResearchService, session: AsyncSession, outcomes: list[WaveOutcome]
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
            source = await service._upsert_source(session, url, item.get("title"))
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


async def run_discovery(
    service: ResearchService, session: AsyncSession, plan: ResearchPlan, profile: Any
) -> dict[str, Any]:
    entries = plan.planned_queries or []
    waves, used = service._planned_waves(entries)

    results: list[dict[str, Any]] = []
    outcomes = await service._fetch_waves(waves)
    recorded, official_domains, run_ids = await service._record_outcomes(session, outcomes)
    results.extend(recorded)

    page2_runs = 0
    official_site_query: str | None = None
    # Wave 2 (inside the discovery budget): official-site query for a domain
    # known from wave-1 discovery results + page-2 for top queries.
    if service._circuit.allow() and waves:
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
            extra_outcomes = await service._fetch_waves(extra)
            extra_recorded, _, extra_ids = await service._record_outcomes(session, extra_outcomes)
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
        "circuit_state": service._circuit.state,
        "career": {
            **career_summary(results),
            **await record_career_evidence(session, search_run_ids=run_ids),
        },
        "policy": {
            **policy_summary(results),
            **await record_policy_evidence(session, search_run_ids=run_ids),
        },
    }
    if not service._circuit.allow():
        # serpapi_docs rule 15: provider failing -> complete discovery from
        # recent evidence inside its freshness window, labeled as cached.
        fallback = await service._recent_evidence_fallback(session)
        output.update({"circuit_open": True, **fallback})
    return output


async def recent_evidence_fallback(session: AsyncSession) -> dict[str, Any]:
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


async def upsert_source(
    session: AsyncSession, url: str | None, title: str | None
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


async def result_stats(
    session: AsyncSession, run_ids: list[uuid.UUID]
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


async def run_program_queries(
    service: ResearchService,
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
    outcomes = await service._fetch_waves(waves)
    _recorded, _, run_ids = await service._record_outcomes(session, outcomes)
    results_stored, sources = await service._result_stats(session, run_ids)
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


async def run_funding_queries(
    service: ResearchService,
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
    countries = await service._profile_countries(session, profile)
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

    outcomes = await service._fetch_waves(waves)
    _recorded, _, run_ids = await service._record_outcomes(session, outcomes)
    results_stored, sources = await service._result_stats(session, run_ids)
    return {
        "funding_queries_run": len([o for o in outcomes if not o.skipped]),
        "funding_queries": [w.q for w in waves],
        "funding_budget": MAX_FUNDING_QUERIES,
        "funding_budget_remaining": remaining - len(waves),
        "funding_results_stored": results_stored,
        "funding_sources": sources,
        "search_run_ids": [str(r) for r in run_ids],
    }


async def search_locale_for(service: ResearchService, session: AsyncSession, profile: Any) -> dict[str, Any]:
    """SerpApi localization params (SerpApi "Easy Integration" docs):
    hl/gl/location/google_domain for the profile's primary target country.

    Sent on the wire only — deliberately excluded from the client's cache
    key so the warm 6-hour cache stays valid across locales (the search
    budget is the scarce resource). English UI, US fallback.
    """
    countries = await service._profile_countries(session, profile)
    code = countries[0].strip().lower() if countries else "us"
    locale: dict[str, Any] = {"hl": "en", "gl": code, "google_domain": "google.com"}
    name = country_display(countries[0].strip()) if countries else ""
    if name and name != countries[0].strip():
        # `location` only for known country names: SerpApi rejects unknown
        # location strings, which must never fail a run.
        locale["location"] = name
    return locale


async def profile_countries(session: AsyncSession, profile: Any) -> list[str]:
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
