"""Run-level provider resilience and program canonicalization.

Covers TEST_PLAN §Required test cases that no other suite exercises at run
level:

- "SerpApi timeout -> partial/cached evidence if valid; no UI crash"
- "Provider rate/credit error -> friendly error and cached evidence where valid"
- "Duplicate program -> canonicalization prevents duplicate program records"

Everything here is offline: each test injects a fake SerpApi client, so no live
SerpApi or LLM call is ever made (LLM providers are disabled by `live_db`).
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select

from app.core.config import get_settings
from app.db.models import (
    Institution,
    Program,
    ResearchPlan,
    ResearchPlanStep,
    RunStatus,
    SearchRun,
)
from app.services.profile import get_or_create_profile
from app.services.research.orchestrator import ResearchService
from app.services.serpapi.client import SerpApiError, SerpApiResult

# A tiny breaker makes the circuit-open path deterministic: the first planned
# waves alone must be enough to open it, whatever the planner emits.
CIRCUIT_THRESHOLD = 2


@pytest.fixture
async def api(db_session: Any) -> AsyncClient:
    """HTTP client wired to the ASGI app (same scratch database as db_session)."""
    from app.main import app

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        yield client


async def _seed_valid_profile(session: Any) -> Any:
    """Self-seeding: the test must pass alone, not only after another test
    filled the shared demo profile (validate_profile needs CGPA or
    percentage; the planner needs a country)."""
    profile = await get_or_create_profile(session)
    if profile.cgpa is None and profile.percentage is None:
        profile.cgpa = Decimal("8.1")
        profile.cgpa_scale = Decimal("10")
    if not profile.institution_country_code:
        profile.institution_country_code = "DE"
    await session.commit()
    return profile


def _settings_with_small_breaker() -> Any:
    return get_settings().model_copy(update={"serpapi_circuit_threshold": CIRCUIT_THRESHOLD})


class _FailingSerpApi:
    """Every search fails with the given provider message (no network)."""

    def __init__(self, message: str) -> None:
        self.message = message
        self.calls: list[str] = []

    async def search(
        self,
        engine: str,
        q: str,
        *,
        parameters: dict[str, Any] | None = None,
        cache: dict[str, dict[str, Any]] | None = None,
        locale: dict[str, dict[str, Any]] | None = None,
    ) -> SerpApiResult:
        self.calls.append(q)
        raise SerpApiError("PROVIDER_UNAVAILABLE", self.message, retryable=True)


async def _run_with(provider: _FailingSerpApi, db_session: Any) -> tuple[ResearchPlan, dict[str, Any]]:
    profile = await _seed_valid_profile(db_session)
    service = ResearchService(settings=_settings_with_small_breaker(), serpapi=provider)  # type: ignore[arg-type]
    plan = await service.create_plan(db_session, profile.id, {"intake_year": 2027})
    plan_id = plan.id
    await service.execute_plan(plan_id)
    db_session.expire_all()
    fresh = await db_session.get(ResearchPlan, plan_id)
    assert fresh is not None
    steps = (
        await db_session.execute(
            select(ResearchPlanStep).where(ResearchPlanStep.research_plan_id == plan_id)
        )
    ).scalars().all()
    return fresh, {s.step_key: s for s in steps}


async def _search_runs_after(db_session: Any, before: set[uuid.UUID]) -> list[SearchRun]:
    rows = (await db_session.execute(select(SearchRun))).scalars().all()
    return [r for r in rows if r.id not in before]


async def _search_run_ids(db_session: Any) -> set[uuid.UUID]:
    return {r.id for r in (await db_session.execute(select(SearchRun))).scalars().all()}


# ------------------------------------------------------- SerpApi timeout


async def test_serpapi_timeout_completes_the_run_from_cached_evidence(
    db_session: Any,
) -> None:
    """TEST_PLAN: "SerpApi timeout -> partial/cached evidence if valid; no UI crash."""
    provider = _FailingSerpApi("timed out after 10s waiting for SerpApi response")
    before = await _search_run_ids(db_session)

    plan, steps = await _run_with(provider, db_session)

    # The run must finish, not die: every critical step still ran.
    assert plan.status in (RunStatus.SUCCEEDED, RunStatus.PARTIAL), plan.error_message
    assert plan.completed_at is not None
    for key in (
        "validate_profile",
        "plan_queries",
        "evaluate_requirements",
        "score_fit",
        "build_strategy",
    ):
        assert steps[key].status == RunStatus.SUCCEEDED, f"{key}: {steps[key].error_message}"

    # The breaker opened and the discovery step labelled its fallback as
    # cached evidence (rule 15), never as fresh live results.
    discovery = steps["discovery_search"].output
    assert discovery is not None
    assert discovery.get("circuit_state") == "open"
    assert discovery.get("circuit_open") is True
    assert discovery.get("cached") is True
    assert discovery.get("source") == "recent_evidence"

    # Every provider request was recorded honestly as a failed search with a
    # stable error code; nothing was fabricated and no traceback leaked.
    failed = [r for r in await _search_runs_after(db_session, before) if r.status == RunStatus.FAILED]
    assert failed, "provider failures must be recorded as FAILED search runs"
    for run in failed:
        assert run.error_code == "PROVIDER_UNAVAILABLE"
        assert "timed out" in (run.error_message or "")
        assert "Traceback" not in (run.error_message or "")
    survivors = [
        r for r in await _search_runs_after(db_session, before) if r.status != RunStatus.FAILED
    ]
    assert not survivors, "an outage must not invent successful searches"

    # Provider spend is bounded by the breaker plus the concurrency window.
    assert len(provider.calls) <= CIRCUIT_THRESHOLD + get_settings().serpapi_max_concurrency


# ------------------------------------------- provider rate/credit error


async def test_provider_rate_limit_is_friendly_and_served_by_the_api(
    db_session: Any, api: AsyncClient
) -> None:
    """TEST_PLAN: "Provider rate/credit error -> friendly error and cached
    evidence where valid." Structured error codes (no stack traces) reach the
    run/events API, which keeps serving the run instead of crashing."""
    provider = _FailingSerpApi("HTTP 429 rate limit exceeded - quota exhausted")
    before = await _search_run_ids(db_session)

    plan, steps = await _run_with(provider, db_session)
    plan_id = plan.id

    assert plan.status in (RunStatus.SUCCEEDED, RunStatus.PARTIAL), plan.error_message

    # Friendly means structured: per-query error codes, never raw tracebacks.
    discovery_output = steps["discovery_search"].output
    assert discovery_output is not None
    searches = discovery_output.get("searches") or []
    assert searches, "planned searches must be reported back to the UI"
    for entry in searches:
        assert "error" in entry or "skipped" in entry, entry
        assert "Traceback" not in str(entry)
    assert any(entry.get("error") == "PROVIDER_UNAVAILABLE" for entry in searches)

    failed = [r for r in await _search_runs_after(db_session, before) if r.status == RunStatus.FAILED]
    assert failed and any("429" in (r.error_message or "") for r in failed)

    # No UI crash: the run and its events stay readable over HTTP.
    run = await api.get(f"/api/v1/research/runs/{plan_id}")
    assert run.status_code == 200, run.text
    body = run.json()
    assert body["id"] == str(plan_id)
    assert body["status"] in ("SUCCEEDED", "PARTIAL")
    assert body["error_message"] in (None, "")

    events = await api.get(f"/api/v1/research/runs/{plan_id}/events")
    assert events.status_code == 200, events.text
    by_key = {s["step_key"]: s for s in events.json()["steps"]}
    assert by_key["discovery_search"]["status"] in ("SUCCEEDED", "PARTIAL")
    event_searches = by_key["discovery_search"]["output"]["searches"]
    assert any(e.get("error") == "PROVIDER_UNAVAILABLE" for e in event_searches)


# ------------------------------------------------- duplicate programs


class _DuplicateTitleSerpApi:
    """Discovery results whose titles collide (same program, two sources) plus
    a title that is already in the local catalog."""

    DUPLICATE = "M.Sc. Quantum Robotics"
    PREEXISTING = "M.Sc. Preexisting Systems"

    async def search(
        self,
        engine: str,
        q: str,
        *,
        parameters: dict[str, Any] | None = None,
        cache: dict[str, dict[str, Any]] | None = None,
        locale: dict[str, dict[str, Any]] | None = None,
    ) -> SerpApiResult:
        items = [
            (self.DUPLICATE, "https://www.tum.de/en/studies/quantum-robotics"),
            (self.DUPLICATE, "https://www.uni-stuttgart.de/study/quantum-robotics"),
            (self.PREEXISTING, "https://www.tum.de/en/studies/preexisting-systems"),
        ]
        organic = [
            {
                "title": title,
                "link": url,
                "snippet": "Program details.",
                "displayed_link": url.removeprefix("https://"),
            }
            for title, url in items
        ]
        return SerpApiResult(
            engine=engine,
            query=q,
            parameters=parameters or {},
            raw={},
            organic_results=organic,
            search_id=f"fake-{engine}",
            duration_ms=1,
        )


async def test_duplicate_program_titles_are_canonicalized_into_one_record(
    db_session: Any,
) -> None:
    """TEST_PLAN: "Duplicate program -> canonicalization prevents duplicate
    program records" (within one run and against the existing catalog)."""
    profile = await _seed_valid_profile(db_session)

    # A catalog record that discovery will see again under the same title.
    institution = Institution(
        canonical_name="Preexisting University",
        normalized_name="preexisting university",
        domain="preexisting.example.edu",
    )
    db_session.add(institution)
    await db_session.flush()
    db_session.add(
        Program(
            institution_id=institution.id,
            canonical_name=_DuplicateTitleSerpApi.PREEXISTING,
            normalized_name=_DuplicateTitleSerpApi.PREEXISTING.strip().lower(),
        )
    )
    await db_session.commit()

    provider = _DuplicateTitleSerpApi()
    service = ResearchService(  # type: ignore[arg-type]
        settings=_settings_with_small_breaker(), serpapi=provider
    )
    plan = await service.create_plan(db_session, profile.id, {"intake_year": 2027})
    plan_id = plan.id
    await service.execute_plan(plan_id)
    db_session.expire_all()

    fresh = await db_session.get(ResearchPlan, plan_id)
    assert fresh is not None
    assert fresh.status in (RunStatus.SUCCEEDED, RunStatus.PARTIAL), fresh.error_message
    steps = (
        await db_session.execute(
            select(ResearchPlanStep).where(ResearchPlanStep.research_plan_id == plan_id)
        )
    ).scalars().all()
    by_key = {s.step_key: s for s in steps}
    assert by_key["normalize_programs"].status == RunStatus.SUCCEEDED

    # Only the genuinely new program was created: the colliding title and the
    # pre-existing catalog record were skipped, not duplicated.
    output = by_key["normalize_programs"].output
    assert output is not None
    assert output.get("programs_created") == 1, output

    async def _count(normalized: str) -> int:
        return int(
            (
                await db_session.execute(
                    select(func.count(Program.id)).where(Program.normalized_name == normalized)
                )
            ).scalar_one()
        )

    assert await _count(_DuplicateTitleSerpApi.DUPLICATE.strip().lower()) == 1
    assert await _count(_DuplicateTitleSerpApi.PREEXISTING.strip().lower()) == 1
