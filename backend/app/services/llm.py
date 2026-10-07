"""Structured-output LLM provider interface."""

from __future__ import annotations

from typing import Any, Protocol, TypeVar

from pydantic import BaseModel, ValidationError

from app.core.config import Settings

T = TypeVar("T", bound=BaseModel)


class LLMError(Exception):
    pass


class OpenAICompatibleLLMProvider:
    """Works for OpenRouter and Groq (both OpenAI-compatible chat APIs)."""

    def __init__(self, endpoints: list[dict[str, str]]) -> None:
        self._endpoints = endpoints

    async def generate_structured(
        self, input: dict[str, Any], schema: type[T], model_config: dict[str, Any] | None = None
    ) -> T:
        import json as _json
        import logging

        import httpx

        logger = logging.getLogger(__name__)
        last_error: Exception | None = None
        for endpoint in self._endpoints:
            try:
                prompt = _json.dumps(input)
                messages = [
                    {
                        "role": "system",
                        "content": (
                            "You are a structured extraction engine. Respond ONLY with a single JSON object "
                            "matching the requested schema. No prose, no markdown fences."
                        ),
                    },
                    {"role": "user", "content": prompt},
                ]
                async with httpx.AsyncClient(timeout=httpx.Timeout(60.0)) as http:
                    response = await http.post(
                        f"{endpoint['base_url']}/chat/completions",
                        headers={"Authorization": f"Bearer {endpoint['api_key']}"},
                        json={
                            "model": endpoint["model"],
                            "messages": messages,
                            "response_format": {"type": "json_object"},
                        },
                    )
                if response.status_code != 200:
                    raise LLMError(
                        f"{endpoint['provider']} HTTP {response.status_code}: {response.text[:200]}"
                    )
                payload = response.json()
                content = payload["choices"][0]["message"]["content"]
                data = _json.loads(content)
                return schema.model_validate(data)
            except Exception as exc:  # noqa: BLE001 - one bad endpoint must not kill the chain
                # Network blips (DNS/timeouts), HTTP errors, invalid JSON or schema
                # mismatch all fall through to the next configured endpoint.
                logger.info("LLM endpoint %s failed: %r", endpoint["provider"], exc)
                last_error = exc
                continue
        raise LLMError(f"All LLM endpoints failed: {last_error!r}")


class LLMProvider(Protocol):
    async def generate_structured(
        self, input: dict[str, Any], schema: type[T], model_config: dict[str, Any] | None = None
    ) -> T: ...


class NullLLMProvider:
    """Used when no LLM key is configured. Never invents data; always raises for extraction."""

    async def generate_structured(
        self, input: dict[str, Any], schema: type[T], model_config: dict[str, Any] | None = None
    ) -> T:
        raise LLMError("LLM provider not configured")


def get_llm_provider(settings: Settings) -> LLMProvider:
    endpoints = settings.llm_endpoints
    if not endpoints:
        return NullLLMProvider()
    return OpenAICompatibleLLMProvider(endpoints)


async def generate_with_repair(
    provider: LLMProvider,
    raw_generator: Any,  # async callable returning raw JSON-like dict
    schema: type[T],
    *,
    max_attempts: int = 2,
) -> T:
    last_error: Exception | None = None
    for _ in range(max_attempts):
        try:
            raw = await raw_generator()
            return schema.model_validate(raw)
        except (ValidationError, LLMError, ValueError) as exc:
            last_error = exc
    raise LLMError(f"LLM output failed validation after {max_attempts} attempts: {last_error}")
