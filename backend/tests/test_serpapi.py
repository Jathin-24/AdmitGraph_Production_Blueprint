import httpx
import pytest

from app.core.config import Settings
from app.services.serpapi.client import SerpApiClient, SerpApiError


def make_settings() -> Settings:
    return Settings(serpapi_api_key="test-key")


@pytest.mark.asyncio
async def test_google_search_parses_organic_results() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["api_key"] == "test-key"
        assert request.url.params["engine"] == "google"
        return httpx.Response(
            200,
            json={
                "search_metadata": {"id": "abc123"},
                "organic_results": [{"title": "T", "link": "https://x.de", "snippet": "s"}],
            },
        )

    client = SerpApiClient(make_settings(), httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    result = await client.google("MSc AI Germany")
    assert result.search_id == "abc123"
    assert result.result_count == 1
    assert result.cache_hit is False


@pytest.mark.asyncio
async def test_cache_hit_avoids_second_call() -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(200, json={"search_metadata": {}, "organic_results": []})

    cache: dict = {}
    client = SerpApiClient(make_settings(), httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    await client.google("q", cache=cache)
    second = await client.google("q", cache=cache)
    assert calls["n"] == 1
    assert second.cache_hit is True


@pytest.mark.asyncio
async def test_locale_params_sent_on_wire_but_not_in_cache_key() -> None:
    """SerpApi localization (hl/gl/location/google_domain) reaches the request
    while the cache key stays locale-independent, so the warm cache is not
    fragmented (the monthly search budget is the scarce resource)."""
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json={"search_metadata": {}, "organic_results": []})

    cache: dict = {}
    client = SerpApiClient(make_settings(), httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    await client.search(
        "google",
        "q",
        cache=cache,
        locale={"hl": "en", "gl": "de", "location": "Germany", "google_domain": "google.com"},
    )
    assert calls[0].url.params["hl"] == "en"
    assert calls[0].url.params["gl"] == "de"
    assert calls[0].url.params["location"] == "Germany"
    assert calls[0].url.params["google_domain"] == "google.com"

    # Same query, different locale: cache hit, no second provider request.
    second = await client.search("google", "q", cache=cache, locale={"hl": "en", "gl": "us"})
    assert second.cache_hit is True
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_missing_api_key() -> None:
    client = SerpApiClient(Settings(serpapi_api_key=""))
    with pytest.raises(SerpApiError) as exc:
        await client.google("q")
    assert exc.value.code == "PROVIDER_AUTH_ERROR"


@pytest.mark.asyncio
async def test_transient_error_becomes_unavailable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"error": "boom"})

    client = SerpApiClient(make_settings(), httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    with pytest.raises(SerpApiError) as exc:
        await client.google("q")
    assert exc.value.code == "PROVIDER_UNAVAILABLE"
    assert exc.value.retryable is True


@pytest.mark.asyncio
async def test_unsupported_engine_rejected() -> None:
    client = SerpApiClient(make_settings())
    with pytest.raises(SerpApiError) as exc:
        await client.search("bing", "q")
    assert exc.value.code == "UNSUPPORTED_ENGINE"
