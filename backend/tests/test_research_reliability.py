"""Reliability tests for the research run: budgets, circuit breaker, cache and
LLM repair prompts (BACKEND_SPEC "Reliability" + serpapi_docs/SERPAPI_INTEGRATION.md).

Everything here is offline: providers are fakes or httpx.MockTransport handlers.
No test ever calls SerpApi or an LLM endpoint.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from pydantic import BaseModel, ValidationError

from app.core.config import Settings
from app.services.llm import OpenAICompatibleLLMProvider, build_repair_message, generate_with_repair
from app.services.research.orchestrator import ResearchService, SearchWave
from app.services.serpapi.cache import SearchCache
from app.services.serpapi.circuit import CircuitBreaker
from app.services.serpapi.client import SerpApiClient, SerpApiError, SerpApiResult


# --------------------------------------------------------------------------- fakes
class _FakeSerpApi:
    """Counts provider calls. Never touches the network."""

    def __init__(self, *, fail: bool = False) -> None:
        self.calls: list[tuple[str, str]] = []
        self._fail = fail

    async def search(
        self,
        engine: str,
        q: str,
        *,
        parameters: dict[str, Any] | None = None,
        cache: dict[str, dict[str, Any]] | None = None,
        locale: dict[str, Any] | None = None,
    ) -> SerpApiResult:
        self.calls.append((engine, q))
        if self._fail:
            raise SerpApiError("PROVIDER_UNAVAILABLE", "provider down", retryable=True)
        return SerpApiResult(
            engine=engine,
            query=q,
            parameters=parameters or {},
            raw={},
            organic_results=[],
            search_id="fake-search",
            duration_ms=1,
        )


def _service(provider: _FakeSerpApi, *, threshold: int = 5) -> ResearchService:
    service = ResearchService(serpapi=provider)  # type: ignore[arg-type]
    service._circuit = CircuitBreaker(threshold=threshold)
    return service


# ------------------------------------------------------------------- circuit breaker
def test_circuit_opens_at_threshold_and_closes_on_success() -> None:
    breaker = CircuitBreaker(threshold=2)
    assert breaker.allow() is True and breaker.state == "closed"

    breaker.record_failure("PROVIDER_UNAVAILABLE")
    assert breaker.allow() is True and breaker.state == "half_open"

    breaker.record_failure("PROVIDER_UNAVAILABLE")
    assert breaker.allow() is False and breaker.state == "open"

    breaker.record_success()
    assert breaker.allow() is True and breaker.state == "closed"
    assert breaker.consecutive_failures == 0


async def test_open_circuit_skips_remaining_live_searches() -> None:
    threshold = 2
    waves = [SearchWave(engine="google", q=f"query {i}", purpose="discovery") for i in range(6)]
    provider = _FakeSerpApi(fail=True)
    service = _service(provider, threshold=threshold)

    outcomes = await service._fetch_waves(waves)

    # Only `threshold` requests are spent before the breaker opens; the rest are
    # skipped without a provider request (and without a fabricated SearchRun).
    assert len(provider.calls) == threshold
    skipped = [o for o in outcomes if o.skipped]
    assert len(skipped) == len(waves) - threshold
    assert all(o.result is None for o in outcomes)
    assert all(o.error is not None for o in outcomes[:threshold])
    assert {o.wave.q for o in skipped} == {w.q for w in waves[threshold:]}


async def test_healthy_provider_runs_every_planned_wave() -> None:
    waves = [SearchWave(engine="google", q=f"query {i}", purpose="discovery") for i in range(4)]
    provider = _FakeSerpApi()
    service = _service(provider, threshold=1)

    outcomes = await service._fetch_waves(waves)

    assert len(provider.calls) == 4
    assert all(not o.skipped and o.error is None for o in outcomes)
    assert service._circuit.allow() is True


# --------------------------------------------------------------------------- budgets
def _entries(budget: str, count: int) -> list[dict[str, Any]]:
    return [
        {
            "engine": "google",
            "q": f"{budget} query {i}",
            "purpose": budget if budget != "program:req" else "requirements",
            "parameters": {},
            "template": None,
            "budget": budget,
        }
        for i in range(count)
    ]


def test_planned_waves_enforce_per_purpose_budgets() -> None:
    from app.services.research.planner import MAX_DISCOVERY_QUERIES, RUN_BUDGETS

    service = _service(_FakeSerpApi())
    entries = _entries("discovery", 20) + _entries("policy", 5) + _entries("news", 4)
    waves, used = service._planned_waves(entries)

    assert used["discovery"] == MAX_DISCOVERY_QUERIES
    assert used["policy"] == RUN_BUDGETS["policy"]
    assert used["news"] == RUN_BUDGETS["news"]
    assert len(waves) == sum(RUN_BUDGETS[b] for b in ("discovery", "policy", "news"))


def test_planned_waves_bound_program_queries_per_program() -> None:
    from app.services.research.planner import MAX_REQUIREMENTS_QUERIES_PER_PROGRAM

    service = _service(_FakeSerpApi())
    waves, used = service._planned_waves(_entries("program:MSc Test", 9))
    assert used["program:MSc Test"] == MAX_REQUIREMENTS_QUERIES_PER_PROGRAM
    assert len(waves) == MAX_REQUIREMENTS_QUERIES_PER_PROGRAM


def test_planned_waves_round_trip_serialized_planner_output() -> None:
    from types import SimpleNamespace

    from app.services.research.planner import plan_queries

    profile = SimpleNamespace(field_of_study="AI", institution_country_code="DE", graduation_year=2027)
    planned = plan_queries(profile, {"preferred_countries": ["DE"]}, 2027)  # type: ignore[arg-type]
    entries = [q.as_dict() for q in planned]
    waves, _used = _service(_FakeSerpApi())._planned_waves(entries)

    assert [(w.engine, w.purpose, w.budget) for w in waves] == [
        (q.engine, q.purpose, q.budget) for q in planned
    ]
    assert [w.q for w in waves] == [q.q for q in planned]


# ------------------------------------------------------------------------- cache
async def test_cache_falls_back_to_memory_when_redis_is_down(monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom() -> Any:
        raise ConnectionError("redis unreachable")

    monkeypatch.setattr("app.core.redis.get_redis", _boom)

    cache = SearchCache(Settings(serpapi_api_key="k"))
    await cache.set("serpapi:test-key", {"organic_results": [{"title": "T"}]})

    assert await cache.get("serpapi:test-key") == {"organic_results": [{"title": "T"}]}
    assert await cache.get("serpapi:missing") is None
    # A cache outage degrades to the in-process cache; it never fails a run.
    assert cache.backend == "memory"


async def test_identical_searches_hit_the_fallback_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom() -> Any:
        raise ConnectionError("redis unreachable")

    monkeypatch.setattr("app.core.redis.get_redis", _boom)

    requests: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(str(request.url))
        return httpx.Response(
            200, json={"search_metadata": {"id": "abc"}, "organic_results": []}
        )

    settings = Settings(serpapi_api_key="test-key")
    client = SerpApiClient(
        settings,
        httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        cache=SearchCache(settings),
    )
    first = await client.google("same query")
    second = await client.google("same query")

    assert len(requests) == 1
    assert first.cache_hit is False
    assert second.cache_hit is True


# ------------------------------------------------------------------- LLM repair prompt
class _Payload(BaseModel):
    value: int


def test_repair_message_carries_error_output_and_schema() -> None:
    with pytest.raises(ValidationError) as excinfo:
        _Payload.model_validate({"value": "not-an-int"})

    message = build_repair_message(excinfo.value, {"value": "not-an-int"}, _Payload)

    assert "Validation error" in message
    assert "not-an-int" in message  # the invalid output is echoed back
    assert '"value"' in message  # schema reminder
    assert str(excinfo.value) in message


def test_repair_message_truncates_huge_payloads() -> None:
    with pytest.raises(ValidationError) as excinfo:
        _Payload.model_validate({"value": "x"})

    huge = {"value": "y" * 50_000}
    message = build_repair_message(excinfo.value, huge, _Payload)
    assert len(message) < 6000
    assert "truncated" in message


async def test_provider_sends_repair_prompt_after_schema_mismatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bodies: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        invalid = len(bodies) == 1
        content = {"value": "not-an-int"} if invalid else {"value": 7}
        return httpx.Response(
            200, json={"choices": [{"message": {"content": json.dumps(content)}}]}
        )

    real_client = httpx.AsyncClient

    def factory(*args: Any, **kwargs: Any) -> httpx.AsyncClient:
        return real_client(transport=httpx.MockTransport(handler))

    monkeypatch.setattr(httpx, "AsyncClient", factory)

    provider = OpenAICompatibleLLMProvider(
        [{"base_url": "https://llm.test", "api_key": "k", "model": "m", "provider": "fake"}]
    )
    result = await provider.generate_structured({"task": "t"}, _Payload)

    assert result.value == 7
    assert len(bodies) == 2  # first attempt + exactly one repair attempt
    first_messages = bodies[0]["messages"]
    repair_messages = bodies[1]["messages"]
    assert len(first_messages) == 2
    assert len(repair_messages) == 3
    repair_text = repair_messages[-1]["content"]
    assert "Validation error" in repair_text
    assert "Schema reminder" in repair_text


async def test_generate_with_repair_passes_the_repair_message() -> None:
    attempts: list[str | None] = []

    async def raw(repair: str | None = None) -> Any:
        attempts.append(repair)
        return {"value": "first"} if repair is None else {"value": 5}

    result = await generate_with_repair(None, raw, _Payload)  # type: ignore[arg-type]

    assert result.value == 5
    assert len(attempts) == 2
    assert attempts[0] is None  # first attempt is the plain prompt
    assert attempts[1] is not None and "Validation error" in attempts[1]


async def test_generate_with_repair_fails_safely_after_max_attempts() -> None:
    from app.services.llm import LLMError

    async def raw(repair: str | None = None) -> Any:
        return {"value": "always wrong"}

    with pytest.raises(LLMError) as excinfo:
        await generate_with_repair(None, raw, _Payload, max_attempts=2)  # type: ignore[arg-type]
    assert "failed validation after 2 attempts" in str(excinfo.value)


async def test_generate_with_repair_accepts_a_sync_generator_returning_a_dict() -> None:
    """Regression: awaiting a plain dict raised "'dict' object can't be awaited"."""

    def raw(repair: str | None = None) -> dict[str, Any]:
        return {"value": "bad"} if repair is None else {"value": 9}

    result = await generate_with_repair(None, raw, _Payload)  # type: ignore[arg-type]

    assert result.value == 9  # one plain attempt + one repair attempt
