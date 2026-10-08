from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field
from typing import Any

import httpx
from tenacity import (
    retry,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential_jitter,
)

from app.core.config import Settings
from app.services.serpapi.cache import SearchCache

SERPAPI_ENDPOINT = "https://serpapi.com/search"

SUPPORTED_ENGINES = {
    "google": "google",
    "google_jobs": "google_jobs",
    "google_news": "google_news",
    "google_scholar": "google_scholar",
    "google_trends": "google_trends",
    "google_maps": "google_maps",
}


class SerpApiError(Exception):
    def __init__(self, code: str, message: str, retryable: bool = False) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable


class _TransientProviderError(Exception):
    pass


def _is_transient(exc: BaseException) -> bool:
    return isinstance(exc, _TransientProviderError)


@dataclass
class SerpApiResult:
    engine: str
    query: str
    parameters: dict[str, Any]
    raw: dict[str, Any]
    organic_results: list[dict[str, Any]] = field(default_factory=list)
    news_results: list[dict[str, Any]] = field(default_factory=list)
    jobs_results: list[dict[str, Any]] = field(default_factory=list)
    search_metadata: dict[str, Any] = field(default_factory=dict)
    search_id: str | None = None
    duration_ms: int = 0
    cache_hit: bool = False

    @property
    def result_count(self) -> int:
        return len(self.organic_results) + len(self.news_results) + len(self.jobs_results)


class SerpApiClient:
    def __init__(
        self,
        settings: Settings,
        http_client: httpx.AsyncClient | None = None,
        cache: SearchCache | None = None,
    ) -> None:
        self._settings = settings
        self._http = http_client
        # Redis-with-fallback cache (serpapi_docs rule 9). Optional: callers may
        # pass their own, and tests may pass a plain dict via search(cache=...).
        self._cache = cache

    async def _get_http(self) -> httpx.AsyncClient:
        if self._http is None:
            self._http = httpx.AsyncClient(timeout=httpx.Timeout(30.0))
        return self._http

    def cache_key(self, engine: str, params: dict[str, Any]) -> str:
        stable = json.dumps({"engine": engine, **params}, sort_keys=True)
        return "serpapi:" + hashlib.sha256(stable.encode()).hexdigest()

    async def search(
        self,
        engine: str,
        q: str,
        *,
        parameters: dict[str, Any] | None = None,
        cache: dict[str, dict[str, Any]] | None = None,
        locale: dict[str, Any] | None = None,
    ) -> SerpApiResult:
        if engine not in SUPPORTED_ENGINES:
            raise SerpApiError("UNSUPPORTED_ENGINE", f"Engine '{engine}' is not supported")
        if not q or not q.strip():
            raise SerpApiError("VALIDATION_ERROR", "Query 'q' is required")
        params: dict[str, Any] = {"engine": engine, "q": q, "output": "json", **(parameters or {})}
        if locale:
            # Localization (SerpApi hl/gl/location/google_domain) rides on the
            # request but NOT on the cache key: presentation hints must not
            # fragment the warm 6h cache — cache hits are the budget control.
            params.update(locale)

        key = self.cache_key(engine, {"q": q, **(parameters or {})})
        if cache is not None and key in cache:
            cached = cache[key]
            return self._result_from_payload(engine, q, params, cached, cache_hit=True)
        if self._cache is not None:
            adapter_hit = await self._cache.get(key)
            if adapter_hit is not None:
                return self._result_from_payload(engine, q, params, adapter_hit, cache_hit=True)

        started = time.perf_counter()
        try:
            payload = await self._search_with_retry(params)
        except _TransientProviderError as exc:
            raise SerpApiError("PROVIDER_UNAVAILABLE", str(exc), retryable=True) from exc
        duration_ms = int((time.perf_counter() - started) * 1000)
        if cache is not None:
            cache[key] = payload
        if self._cache is not None:
            await self._cache.set(key, payload)
        metadata = payload.get("search_metadata", {})
        return SerpApiResult(
            engine=engine,
            query=q,
            parameters=params,
            raw=payload,
            organic_results=payload.get("organic_results", []),
            news_results=payload.get("news_results", []),
            jobs_results=payload.get("jobs_results", []),
            search_metadata=metadata,
            search_id=metadata.get("id"),
            duration_ms=duration_ms,
        )

    @staticmethod
    def _result_from_payload(
        engine: str,
        q: str,
        params: dict[str, Any],
        cached: dict[str, Any],
        *,
        cache_hit: bool,
    ) -> SerpApiResult:
        metadata = cached.get("search_metadata", {})
        return SerpApiResult(
            engine=engine,
            query=q,
            parameters=params,
            raw=cached,
            organic_results=cached.get("organic_results", []),
            news_results=cached.get("news_results", []),
            jobs_results=cached.get("jobs_results", []),
            search_metadata=metadata,
            search_id=metadata.get("id"),
            cache_hit=cache_hit,
        )

    async def google(
        self, q: str, *, cache: dict[str, dict[str, Any]] | None = None, **parameters: Any
    ) -> SerpApiResult:
        return await self.search("google", q, parameters=parameters, cache=cache)

    async def google_jobs(
        self, q: str, *, cache: dict[str, dict[str, Any]] | None = None, **parameters: Any
    ) -> SerpApiResult:
        return await self.search("google_jobs", q, parameters=parameters, cache=cache)

    async def google_news(
        self, q: str, *, cache: dict[str, dict[str, Any]] | None = None, **parameters: Any
    ) -> SerpApiResult:
        return await self.search("google_news", q, parameters=parameters, cache=cache)

    async def google_scholar(
        self, q: str, *, cache: dict[str, dict[str, Any]] | None = None, **parameters: Any
    ) -> SerpApiResult:
        return await self.search("google_scholar", q, parameters=parameters, cache=cache)

    async def google_trends(
        self, q: str, *, cache: dict[str, dict[str, Any]] | None = None, **parameters: Any
    ) -> SerpApiResult:
        return await self.search("google_trends", q, parameters=parameters, cache=cache)

    async def google_maps(
        self, q: str, *, cache: dict[str, dict[str, Any]] | None = None, **parameters: Any
    ) -> SerpApiResult:
        return await self.search("google_maps", q, parameters=parameters, cache=cache)

    @retry(
        retry=retry_if_exception(_is_transient),
        stop=stop_after_attempt(3),
        wait=wait_exponential_jitter(initial=0.5, max=8),
        reraise=True,
    )
    async def _search_with_retry(self, params: dict[str, Any]) -> dict[str, Any]:
        if not self._settings.serpapi_api_key:
            raise SerpApiError("PROVIDER_AUTH_ERROR", "SERPAPI_API_KEY is not configured")
        http = await self._get_http()
        try:
            response = await http.get(
                SERPAPI_ENDPOINT,
                params={**params, "api_key": self._settings.serpapi_api_key},
            )
        except httpx.TimeoutException as exc:
            raise _TransientProviderError(str(exc)) from exc
        except httpx.TransportError as exc:
            raise _TransientProviderError(str(exc)) from exc

        if response.status_code == 429 or response.status_code >= 500:
            raise _TransientProviderError(f"HTTP {response.status_code}")
        if response.status_code in (401, 403):
            raise SerpApiError("PROVIDER_AUTH_ERROR", f"HTTP {response.status_code}")
        if response.status_code == 400:
            raise SerpApiError("PROVIDER_VALIDATION_ERROR", response.text[:500])
        if response.status_code != 200:
            raise SerpApiError("PROVIDER_ERROR", f"HTTP {response.status_code}")

        try:
            payload: dict[str, Any] = response.json()
        except ValueError as exc:
            raise SerpApiError("PROVIDER_PARSE_ERROR", "Invalid JSON from SerpApi") from exc

        if "error" in payload:
            message = str(payload["error"])
            if "Invalid API key" in message or "run out of searches" in message:
                raise SerpApiError("PROVIDER_AUTH_ERROR", message)
            raise SerpApiError("PROVIDER_ERROR", message)
        return payload
